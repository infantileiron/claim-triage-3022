ROLES=('admin','analyst','viewer')
STATES=('queued','running','done','failed')
DECISIONS=('pending','valid','flagged')
LABELS=(0,1)

# 关联筛查（拆单报销）口径
SCREENING_STATES=('pending','running','done','failed')
GROUP_STATUSES=('pending','draft','approved','below_limit')
SUPERSEDE_REASONS=('carried','split','merge','replaced','rescreen')
LINK_WINDOW_HOURS=24
