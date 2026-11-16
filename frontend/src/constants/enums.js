export const ROLES=['admin','analyst','viewer'];export const STATES=['queued','running','done','failed'];export const DECISIONS=['pending','valid','flagged'];export const LABELS=[0,1];
export const SCREENING_STATES=['pending','running','done','failed'];
export const GROUP_STATUSES=['pending','draft','approved','below_limit'];
export const STALE_REASONS={carried:'沿用',split:'已拆分',merge:'已合并',replaced:'已替换',rescreen:'重新筛查'};
export const STATUS_TEXT={pending:'待处理',running:'处理中',done:'已完成',failed:'失败',draft:'已填待签',approved:'已签核',below_limit:'未超限额'};
export const EVENT_TEXT={created:'新建组',carried:'沿用原结论',replaced:'成员或金额变化，重新待核',stale_split:'旧组因拆分过期',stale_merge:'旧组因合并过期',stale_replaced:'旧组被替换过期',carried_out:'结论已沿用至新版本',reviewed:'填写组级复核',approved:'组级签核'};
export const LINK_WINDOW_HOURS=24;
