"""关联筛查（拆单报销）。

口径：
- 仅“报销人 + 商户 + 消费时间”齐全的样本参与连边；信息不全的旧样本单列，不臆测。
- 同人、同商户、消费时间相差不超过 24 小时的两笔样本连一条边。
- 连边后按传递关系聚合（A-B、B-C 可跨日合成一组，即使 A-C 超过 24 小时）。
- 组内金额合计严格超过限额才进入组级复核；未超限额的组保留但不产生复核任务。
- 每次重算都是一个新筛查版本。成员集合完全一致的组沿用旧结论；发生拆分/合并/替换的
  旧组保留原结论并标记 stale 与原因，新组重新待核。
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib

from src.config.database import Session
from src.models.entities import (
    Audit, Claim, CorrelationGroup, CorrelationState, GroupEvent, GroupMember, Screening,
)
from src.constants.enums import LINK_WINDOW_HOURS

LINK_WINDOW_SECONDS = LINK_WINDOW_HOURS * 3600


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def clean_time(value):
    """统一存成 UTC 朴素时间，避免 MySQL/SQLite 对时区的不同处理。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


def missing_fields(claim):
    missing = []
    if not clean_text(claim.claimant):
        missing.append('claimant')
    if not clean_text(claim.merchant):
        missing.append('merchant')
    if claim.occurred_at is None:
        missing.append('occurred_at')
    return missing


def group_key(claim_ids):
    return hashlib.sha256(','.join(str(i) for i in sorted(claim_ids)).encode()).hexdigest()


def _bucket_key(claim):
    return (clean_text(claim.claimant), clean_text(claim.merchant))


def build_groups(claims):
    """返回 {'groups': [...], 'incomplete': [...], 'eligible_count': n}。

    groups 只包含至少两笔样本的连通分量；单笔不构成拆单关联。
    """
    eligible = [c for c in claims if not missing_fields(c)]
    incomplete = []
    for c in claims:
        missing = missing_fields(c)
        if missing:
            incomplete.append({
                'id': c.id,
                'code': c.code,
                'amount': c.amount,
                'claimant': clean_text(c.claimant),
                'merchant': clean_text(c.merchant),
                'occurred_at': c.occurred_at.isoformat() if c.occurred_at else None,
                'missing': missing,
            })

    parent = {c.id: c.id for c in eligible}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    buckets = defaultdict(list)
    for c in eligible:
        buckets[_bucket_key(c)].append(c)

    for bucket in buckets.values():
        bucket.sort(key=lambda c: (c.occurred_at, c.id))
        for i, left in enumerate(bucket):
            for right in bucket[i + 1:]:
                gap = abs((right.occurred_at - left.occurred_at).total_seconds())
                if gap > LINK_WINDOW_SECONDS:
                    # 已按时间排序，后面的间隔只会更大。
                    break
                union(left.id, right.id)

    components = defaultdict(list)
    for c in eligible:
        components[find(c.id)].append(c)

    groups = []
    for members in components.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda c: (c.occurred_at, c.id))
        chain = []
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                gap = (right.occurred_at - left.occurred_at).total_seconds()
                if gap > LINK_WINDOW_SECONDS:
                    break
                chain.append({
                    'from_id': left.id,
                    'from_code': left.code,
                    'from_at': left.occurred_at.isoformat(),
                    'to_id': right.id,
                    'to_code': right.code,
                    'to_at': right.occurred_at.isoformat(),
                    'gap_hours': round(gap / 3600, 3),
                })
        groups.append({
            'claim_ids': [c.id for c in members],
            'members': members,
            'claimant': clean_text(members[0].claimant),
            'merchant': clean_text(members[0].merchant),
            'total_amount': round(sum(float(c.amount or 0) for c in members), 2),
            'chain': chain,
        })

    groups.sort(key=lambda g: (g['members'][0].occurred_at, g['claim_ids'][0]))
    return {'groups': groups, 'incomplete': incomplete, 'eligible_count': len(eligible)}


def lock_correlation(session):
    """所有会改变分组口径的写操作都先拿这一行的锁，串行化重算。"""
    row = session.query(CorrelationState).filter_by(id=1).with_for_update().first()
    if row is None:
        row = CorrelationState(id=1, revision=0, updated=now_iso())
        session.add(row)
        session.flush()
    row.revision = (row.revision or 0) + 1
    row.updated = now_iso()
    return row


def ensure_correlation_state(session):
    if session.get(CorrelationState, 1) is None:
        session.add(CorrelationState(id=1, revision=0, updated=now_iso()))
        session.commit()


def latest_done_screening(session, exclude_id=None):
    """已完成筛查按完成时间排序；先到先完成，避免慢任务用旧基线覆盖新分组。"""
    query = session.query(Screening).filter(Screening.state == 'done')
    if exclude_id is not None:
        query = query.filter(Screening.id != exclude_id)
    return query.order_by(Screening.finished_at.desc(), Screening.id.desc()).first()


def _load_old_groups(session, screening):
    if screening is None:
        return [], {}, {}
    groups = (session.query(CorrelationGroup)
              .filter_by(screening_id=screening.id)
              .with_for_update()
              .order_by(CorrelationGroup.id)
              .all())
    member_sets = {}
    member_amounts = {}
    for group in groups:
        members = session.query(GroupMember).filter_by(group_id=group.id).all()
        member_sets[group.id] = frozenset(m.claim_id for m in members)
        member_amounts[group.id] = {m.claim_id: round(float(m.amount or 0), 2) for m in members}
    return groups, member_sets, member_amounts


def _delete_groups_of_screening(session, screening_id):
    """重试时先清掉本筛查可能残留的分组，唯一键再兜底，不会重复建组。"""
    group_ids = [row[0] for row in session.query(CorrelationGroup.id).filter_by(screening_id=screening_id).all()]
    if group_ids:
        session.query(GroupMember).filter(GroupMember.group_id.in_(group_ids)).delete(synchronize_session=False)
    session.query(GroupEvent).filter(GroupEvent.screening_id == screening_id).delete(synchronize_session=False)
    session.query(CorrelationGroup).filter_by(screening_id=screening_id).delete(synchronize_session=False)
    session.flush()


def _best_old_overlap(claim_ids, old_groups, old_sets):
    best = None
    best_overlap = 0
    for group in old_groups:
        overlap = len(old_sets[group.id] & claim_ids)
        if overlap > best_overlap:
            best, best_overlap = group, overlap
    return best


def _same_basis(comp, old_amounts):
    """成员集合一致且每笔金额一致，才视为真正未变，可沿用旧结论。"""
    new_members = {m.id: round(float(m.amount or 0), 2) for m in comp['members']}
    return old_amounts == new_members


def apply_screening(session, screening, actor='system'):
    """在当前事务内完成一次分组切换；调用方负责 commit/rollback。

    先拿关联锁再找上一版，保证并发补录/筛查时都以“当前已生效版本”为基线。
    """
    state = lock_correlation(session)
    screening.revision = state.revision

    # 重算幂等：先清空本筛查自己的旧结果。
    _delete_groups_of_screening(session, screening.id)

    claims = session.query(Claim).order_by(Claim.id).all()
    built = build_groups(claims)
    previous = latest_done_screening(session, exclude_id=screening.id)
    if screening.trigger == 'claim_update':
        # 补录触发的筛查继承当前生效限额；必须在锁内重读，避免与并发筛查错位。
        screening.limit_amount = previous.limit_amount if previous is not None else screening.limit_amount
    old_groups, old_sets, old_amounts = _load_old_groups(session, previous)
    stamp = now_iso()
    # 限额变了，旧组的“超限与否”依据不再成立，旧结论不可沿用。
    carry_allowed = previous is not None and float(previous.limit_amount) == float(screening.limit_amount)

    new_rows = []
    changed_old = {}
    for comp in built['groups']:
        claim_ids = frozenset(comp['claim_ids'])
        over_limit = comp['total_amount'] > float(screening.limit_amount)
        exact = next((g for g in old_groups if old_sets[g.id] == claim_ids), None)
        overlap = _best_old_overlap(claim_ids, old_groups, old_sets)
        same_basis = (carry_allowed and exact is not None
                      and _same_basis(comp, old_amounts[exact.id]))

        if not over_limit:
            status, decision, note, reason = 'below_limit', 'pending', '', 'carried'
        elif same_basis and exact.status != 'below_limit':
            # 成员和金额都没变：沿用旧结论（含已签核），旧组继续有效。
            status, decision, note, reason = exact.status, exact.decision, exact.note, 'carried'
        else:
            status, decision, note, reason = 'pending', 'pending', '', ''

        if exact is not None:
            changed_old[exact.id] = 'carried' if same_basis else 'replaced'

        group = CorrelationGroup(
            screening_id=screening.id,
            group_key=group_key(claim_ids),
            total_amount=comp['total_amount'],
            member_count=len(claim_ids),
            status=status,
            decision=decision,
            note=note,
            stale=False,
            stale_reason=reason,
            carried_from=exact.id if same_basis else None,
            review_version=1,
            created=stamp,
            updated=stamp,
            chain=comp['chain'],
        )
        session.add(group)
        session.flush()

        for member in comp['members']:
            session.add(GroupMember(
                group_id=group.id,
                claim_id=member.id,
                code=member.code,
                amount=member.amount,
                claimant=clean_text(member.claimant),
                merchant=clean_text(member.merchant),
                occurred_at=member.occurred_at,
            ))

        session.add(GroupEvent(
            screening_id=screening.id,
            group_id=group.id,
            related_group_id=exact.id if exact is not None else (overlap.id if overlap else None),
            claim_id=None,
            event='carried' if same_basis else ('replaced' if exact is not None else 'created'),
            detail={
                'claim_ids': sorted(claim_ids),
                'total_amount': comp['total_amount'],
                'over_limit': over_limit,
                'status': status,
                'old_group_ids': [g.id for g in old_groups if old_sets[g.id] & claim_ids],
                'old_decision': exact.decision if exact is not None else None,
            },
            actor=actor,
            created=stamp,
        ))
        new_rows.append((group, comp, claim_ids))

    # 给上一版分组定性：未变、拆分、合并或替换。旧结论一律保留，只标记依据过期。
    for old in old_groups:
        old_ids = old_sets[old.id]
        hits = [(g, ids) for g, _comp, ids in new_rows if old_ids & ids]
        if changed_old.get(old.id) == 'carried' and len(hits) == 1 and hits[0][1] == old_ids:
            old.stale, old.stale_reason, event = False, 'carried', 'carried_out'
        elif len(hits) >= 2:
            old.stale, old.stale_reason, event = True, 'split', 'stale_split'
        elif len(hits) == 1 and old_ids < hits[0][1]:
            old.stale, old.stale_reason, event = True, 'merge', 'stale_merge'
        elif len(hits) == 1:
            old.stale, old.stale_reason, event = True, 'replaced', 'stale_replaced'
        else:
            # 没有任何成员落到新组：原来靠桥接票连成的组被打散，仍属拆分。
            survivors = [c for c in old_ids
                         if any(c in ids for _g, _comp, ids in new_rows)]
            reason = 'split' if not survivors else 'replaced'
            old.stale, old.stale_reason, event = True, reason, 'stale_' + reason
        old.review_version = (old.review_version or 1) + 1  # 旧版本号立即失效
        old.updated = stamp
        session.add(GroupEvent(
            screening_id=screening.id,
            group_id=old.id,
            related_group_id=hits[0][0].id if len(hits) == 1 else None,
            claim_id=None,
            event=event,
            detail={
                'claim_ids': sorted(old_ids),
                'new_group_ids': [g.id for g, _ids in hits],
                'stale_reason': old.stale_reason,
                'old_decision': old.decision,
                'old_status': old.status,
            },
            actor=actor,
            created=stamp,
        ))

    screening.group_count = len(new_rows)
    screening.over_limit_count = sum(1 for g, _c, _i in new_rows if g.status != 'below_limit')
    screening.eligible_count = built['eligible_count']
    screening.incomplete_count = len(built['incomplete'])
    screening.incomplete = built['incomplete']
    screening.total_amount = round(sum(c['total_amount'] for c in built['groups']), 2)
    screening.state = 'done'
    screening.error = ''
    screening.finished_at = now_iso()
    session.flush()
    return screening


def run_screening(screening_id, actor='system'):
    """独立事务执行筛查：状态、分组和事件在同一事务内切换，失败不留半套结果。

    先锁住筛查行再执行，重复投递的第二条消息会等第一条提交后看到 done 而直接跳过；
    若同一筛查被重试，apply_screening 会先清空本筛查残留结果，因此不会重复建组。
    """
    with Session() as session:
        try:
            row = (session.query(Screening)
                   .filter_by(id=screening_id)
                   .with_for_update()
                   .first())
            if row is None or row.state not in ('pending', 'running'):
                session.rollback()
                return
            row.state = 'running'
            row.started_at = now_iso()
            row.error = ''
            session.flush()
            apply_screening(session, row, actor=actor)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - 需要把失败原因留给重试界面
            session.rollback()
            with Session() as failure:
                failed = failure.get(Screening, screening_id)
                if failed is not None and failed.state != 'done':
                    failed.state = 'failed'
                    failed.error = str(exc)[:1000]
                    failed.finished_at = now_iso()
                    failure.commit()


def screening_for_claim_update(session, actor):
    """补录/更正与重新分组在同一事务内完成，由调用方统一 commit。

    新版本限额留空，由 apply_screening 在关联锁内继承当前生效版本，避免并发错位。
    """
    previous = latest_done_screening(session)
    if previous is None:
        return None
    row = Screening(
        limit_amount=previous.limit_amount,
        state='running',
        submitted_by=actor,
        trigger='claim_update',
        created=now_iso(),
        started_at=now_iso(),
    )
    session.add(row)
    session.flush()
    apply_screening(session, row, actor=actor)
    return row


def add_event(session, screening_id, group_id, event, actor, detail=None):
    session.add(GroupEvent(
        screening_id=screening_id,
        group_id=group_id,
        event=event,
        detail=detail or {},
        actor=actor,
        created=now_iso(),
    ))


def record_audit(session, actor, action):
    session.add(Audit(actor=actor, action=action, created=now_iso()))
