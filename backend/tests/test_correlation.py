"""关联筛查的端到端验证：跨日链式合并、桥接票更正后的拆分、签核竞争。

用临时 SQLite + fakeredis 起 FastAPI TestClient，不经 Docker。
运行：PYTHONPATH=/tmp/pydeps:.. python3 -m pytest tests/test_correlation.py -q
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='claim-correlation-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + TMP + '/test.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

import fakeredis  # noqa: E402
import redis  # noqa: E402

fake = fakeredis.FakeRedis(decode_responses=True)
redis.Redis.from_url = staticmethod(lambda *a, **k: fake)  # noqa: E731

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, engine, Session  # noqa: E402
from src.main import app  # noqa: E402
from src.models.entities import Claim, CorrelationGroup, GroupMember, Screening  # noqa: E402
from src.services import correlation  # noqa: E402
from src.services.correlation import build_groups, run_screening  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    fake.flushall()
    with Session() as s:
        from src.models.entities import User
        for role in ('admin', 'analyst', 'viewer'):
            s.add(User(email=role + '@demo.local', role=role, password=hash_password('Demo123!')))
        correlation.ensure_correlation_state(s)
        s.commit()
    with TestClient(app) as c:
        yield c


def login(client, role='analyst'):
    r = client.post('/auth/login', json={'email': role + '@demo.local', 'password': 'Demo123!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def claim(client, headers, code, amount, claimant, merchant, occurred_at, label=None):
    r = client.post('/claims', headers=headers, json={
        'code': code, 'amount': amount, 'distance': 50, 'holiday': False,
        'label': label, 'claimant': claimant, 'merchant': merchant, 'occurred_at': occurred_at,
    })
    assert r.status_code == 200, r.text
    return r.json()


def groups_of(screening_id):
    with Session() as s:
        rows = s.query(CorrelationGroup).filter_by(screening_id=screening_id).order_by(CorrelationGroup.id).all()
        out = []
        for g in rows:
            members = s.query(GroupMember).filter_by(group_id=g.id).all()
            out.append({
                'id': g.id, 'status': g.status, 'decision': g.decision, 'note': g.note,
                'stale': g.stale, 'stale_reason': g.stale_reason, 'version': g.review_version,
                'total': g.total_amount, 'claims': sorted(m.claim_id for m in members),
            })
        return out


def screening(client, headers, limit):
    r = client.post('/screenings', headers=headers, json={'limit_amount': limit})
    assert r.status_code == 200, r.text
    sid = r.json()['id']
    run_screening(sid)  # 测试里同步执行 worker 的那一步
    return client.get('/screenings/' + str(sid), headers=headers).json()


def review(client, headers, group_id, version, decision='valid'):
    return client.put('/groups/%s/review' % group_id, headers=headers, json={
        'decision': decision, 'note': '已核对原票据与行程', 'expected_version': version})


def approve(client, headers, group_id, version):
    return client.post('/groups/%s/approve' % group_id, headers=headers,
                       json={'expected_version': version})


# ---------- 纯算法 ----------
def _claim(code, claimant, merchant, at, amount=100.0):
    c = Claim(code=code, amount=amount, claimant=claimant, merchant=merchant,
              occurred_at=correlation.clean_time(correlation.datetime.fromisoformat(at)), distance=1)
    c.id = abs(hash(code)) % 100000
    return c


def test_cross_day_chain_merges_transitively():
    at = correlation.datetime.fromisoformat
    claims = [
        _claim('A', '张三', '云栖酒店', '2026-11-01T22:00:00'),
        _claim('B', '张三', '云栖酒店', '2026-11-02T20:00:00'),  # A-B 22h
        _claim('C', '张三', '云栖酒店', '2026-11-03T18:00:00'),  # B-C 22h，A-C 44h
        _claim('D', '张三', '云栖酒店', '2026-11-05T18:00:00'),  # 超出窗口，独立
        _claim('E', '李四', '云栖酒店', '2026-11-02T20:00:00'),  # 同商户不同人
    ]
    built = build_groups(claims)
    assert len(built['groups']) == 1
    group = built['groups'][0]
    assert sorted(c.id for c in group['members']) == sorted(c.id for c in claims[:3])
    # A-C 相隔 44h，但通过 B 传递关联仍在同一组，链上两条边都 ≤24h
    gaps = {(e['from_code'], e['to_code']): e['gap_hours'] for e in group['chain']}
    assert ('A', 'B') in gaps and ('B', 'C') in gaps
    assert gaps[('A', 'B')] <= 24 and gaps[('B', 'C')] <= 24


def test_missing_fields_are_listed_separately_not_linked():
    at = correlation.datetime.fromisoformat
    with Session() as s:  # build_groups 直接吃对象
        claims = [_claim('X', '王五', '云栖酒店', '2026-11-01T10:00:00'),
                  _claim('Y', '王五', None, '2026-11-01T11:00:00')]
    built = build_groups(claims)
    assert built['groups'] == []
    assert [row['code'] for row in built['incomplete']] == ['Y']
    assert built['incomplete'][0]['missing'] == ['merchant']


# ---------- API / 端到端 ----------
def test_chain_over_limit_enters_group_review(client):
    h = login(client)
    claim(client, h, 'C1', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'C2', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    detail = screening(client, h, 5000)
    assert detail['state'] == 'done'
    assert detail['group_count'] == 1 and detail['over_limit_count'] == 1
    group = detail['groups'][0]
    assert group['total_amount'] == 6000 and group['status'] == 'pending'
    assert len(group['chain']) == 1 and group['chain'][0]['gap_hours'] == 22


def test_below_limit_group_is_kept_but_not_for_review(client):
    h = login(client)
    claim(client, h, 'C1', 1000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'C2', 1000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    detail = screening(client, h, 5000)
    group = detail['groups'][0]
    assert group['status'] == 'below_limit'
    assert review(client, h, group['id'], group['review_version']).status_code == 409


def test_bridge_correction_splits_appproved_group(client):
    h = login(client)
    a = claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    b = claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    c = claim(client, h, 'C', 3000, '张三', '云栖酒店', '2026-11-03T18:00:00')
    first = screening(client, h, 5000)
    group = first['groups'][0]
    assert group['total_amount'] == 9000
    assert review(client, h, group['id'], group['review_version']).status_code == 200
    ok = approve(client, login(client, 'admin'), group['id'], group['review_version'] + 1)
    assert ok.status_code == 200, ok.text
    # 给桥接票 B 补一个不同的商户 → A-B、B-C 断开，旧组合被拆
    r = client.put('/claims/%s' % b['id'], headers=h, json={
        'code': 'B', 'amount': 3000, 'distance': 50, 'holiday': False,
        'claimant': '张三', 'merchant': '别处餐厅', 'occurred_at': '2026-11-02T20:00:00'})
    assert r.status_code == 200, r.text
    sid = r.json()['screening']['id']
    new = client.get('/screenings/' + str(sid), headers=h).json()
    assert new['groups'] == [] or all(g['total_amount'] < 5000 for g in new['groups'])
    old = groups_of(first['id'])[0]
    assert old['stale'] is True and old['stale_reason'] == 'split'
    assert old['status'] == 'approved' and old['decision'] == 'valid'  # 旧结论保留
    # 旧组不能再被复核/签核
    assert review(client, h, group['id'], group['review_version']).status_code == 409


def test_unchanged_group_keeps_conclusion_and_stale_group_rejects(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    first = screening(client, h, 5000)
    g1 = first['groups'][0]
    assert review(client, h, g1['id'], g1['review_version']).status_code == 200
    assert approve(client, login(client, 'admin'), g1['id'], g1['review_version'] + 1).status_code == 200
    # 新增一笔完全无关的样本后重新筛查：原组未变，沿用已签核结论
    claim(client, h, 'Z', 100, '赵六', '别处餐厅', '2026-11-01T09:00:00')
    second = screening(client, h, 5000)
    g2 = [g for g in second['groups'] if g['total_amount'] == 6000][0]
    assert g2['status'] == 'approved' and g2['decision'] == 'valid'
    # 结论沿版本链一路承接下来，未变组不会被重新待核
    assert g2['carried_from'] is not None and not g2['stale']
    with Session() as s:
        origin = s.get(CorrelationGroup, g1['id'])
        assert origin.stale is False and origin.status == 'approved'


def test_concurrent_approval_writes_to_current_group_only(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    first = screening(client, h, 5000)
    g1 = first['groups'][0]
    assert review(client, h, g1['id'], g1['review_version']).status_code == 200
    admin = login(client, 'admin')
    # 两个签核请求携带同一个版本：只有一个成功，另一个 409，不会把结论写到旧版本
    r1 = approve(client, admin, g1['id'], g1['review_version'] + 1)
    r2 = approve(client, admin, g1['id'], g1['review_version'] + 1)
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    current = groups_of(first['id'])[0]
    assert current['status'] == 'approved' and current['version'] == g1['review_version'] + 2


def test_review_after_rescreen_uses_new_version(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    first = screening(client, h, 5000)
    g1 = first['groups'][0]
    # 拿到 v1 后别人补录了 B 的金额，产生新版本并让旧组过期
    client.put('/claims/%s' % 2, headers=h, json={
        'code': 'B', 'amount': 3500, 'distance': 50, 'holiday': False,
        'claimant': '张三', 'merchant': '云栖酒店', 'occurred_at': '2026-11-02T20:00:00'})
    late = review(client, h, g1['id'], g1['review_version'])
    assert late.status_code == 409


def test_retry_does_not_duplicate_groups(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    detail = screening(client, h, 5000)
    sid = detail['id']
    with Session() as s:
        row = s.get(Screening, sid)
        row.state = 'running'  # 模拟 worker 崩溃后重投
        s.commit()
    run_screening(sid)
    run_screening(sid)  # 重复投递
    assert len(groups_of(sid)) == 1


def test_claim_update_is_atomic_with_rescreen(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    screening(client, h, 5000)
    before = client.get('/claims', headers=h).json()
    # 强制分组阶段失败：样本变更必须回滚，不能留下“改了样本但没重算分组”的半套状态
    original = correlation.apply_screening
    correlation.apply_screening = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
    try:
        r = client.put('/claims/%s' % before[0]['id'], headers=h, json={
            'code': 'A', 'amount': 7777, 'distance': 50, 'holiday': False,
            'claimant': '张三', 'merchant': '云栖酒店', 'occurred_at': '2026-11-01T22:00:00'})
    finally:
        correlation.apply_screening = original
    assert r.status_code == 500
    after = client.get('/claims', headers=h).json()
    assert after[0]['amount'] == before[0]['amount']
    with Session() as s:
        assert s.query(Screening).filter_by(trigger='claim_update').count() == 0


def test_viewer_cannot_start_screening(client):
    h = login(client, 'viewer')
    assert client.post('/screenings', headers=h, json={'limit_amount': 10}).status_code == 403


def test_historical_screening_group_rejects_write(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    first = screening(client, h, 5000)
    second = screening(client, h, 5000)  # 新版本成为当前版本
    assert second['is_current'] is True
    old_group = groups_of(first['id'])[0]
    current_group = groups_of(second['id'])[0]
    assert old_group['id'] != current_group['id']
    r = review(client, h, old_group['id'], old_group['version'])
    assert r.status_code == 409 and '当前筛查版本' in r.json()['detail']


def test_screening_worker_failure_keeps_previous_groups(client):
    h = login(client)
    claim(client, h, 'A', 3000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    claim(client, h, 'B', 3000, '张三', '云栖酒店', '2026-11-02T20:00:00')
    first = screening(client, h, 5000)
    before = groups_of(first['id'])
    r = client.post('/screenings', headers=h, json={'limit_amount': 5000})
    sid = r.json()['id']
    original = correlation.build_groups
    correlation.build_groups = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('build failed'))
    try:
        run_screening(sid)
    finally:
        correlation.build_groups = original
    with Session() as s:
        failed = s.get(Screening, sid)
        assert failed.state == 'failed' and 'build failed' in failed.error
        assert s.query(CorrelationGroup).filter_by(screening_id=sid).count() == 0
    assert groups_of(first['id']) == before  # 旧版本分组原样保留
    # 失败可重试，重试后正常建组且不重复
    retry = client.post('/screenings/%s/retry' % sid, headers=h)
    assert retry.status_code == 200
    run_screening(sid)
    run_screening(sid)
    with Session() as s:
        assert s.get(Screening, sid).state == 'done'
        assert s.query(CorrelationGroup).filter_by(screening_id=sid).count() == 1


def test_incomplete_claim_listed_not_linked(client):
    h = login(client)
    claim(client, h, 'OLD1', 4000, None, None, None)  # 历史样本信息不全
    claim(client, h, 'OLD2', 4000, None, None, None)
    claim(client, h, 'FULL', 4000, '张三', '云栖酒店', '2026-11-01T22:00:00')
    detail = screening(client, h, 5000)
    assert detail['incomplete_count'] == 2
    assert {row['code'] for row in detail['incomplete']} == {'OLD1', 'OLD2'}
    assert detail['groups'] == []
