# 资料约定

原始资料均为本地合成。CSV 使用 UTF-8，首行是字段名。

观察轮次为 2026 年 8 月，合成事项不能用于查真实身份。`frame` 包含全口径目标、厅别、环节与可用渠道；`invites` 是常规邀请及自愿响应，评分 1 和 2 为低分，空分不算零。`draws` 记录独立抽样的分层入样概率和已入样事项的随机渠道分配概率，actual 表示最终完成渠道，空表示无响应；`cross_reason` 区分跨渠道与无联系。`attempts` 包含常规和抽样核验的历史接触，顺序由时间和序号确定，所有拨出/发出都消耗次数。`restrictions` 的 stop 生效后不再联系。`findings` 的 corroborated 表示核验证实具体经历和环节。`audit_orders` 为已经发给核验人员的原始范围，不能把后来的自发回复变为抽样完成。`policy` 写明追加核验限额和建议门槛：分数为低分与否，未响应低分比率在资料允许的区间内变化；至少需要给定数量的环节核验证据并在双侧置信水平下仍超过低分比例门槛。核验单不包含电话号码。
`followup_draws` 是上一批追加核验的实际抽选和接触回执，previous_nonresponse 的概率以首轮抽中且未响应为条件，new_frame 以未入首轮为条件；与 `audit_orders` 同一事项、发出日期相同的记录是同一份安排。`followup_limits` 对两类追加来源分别给出上限，并共同受 policy 中总接触上限约束。

`audit_batches` 是后续名册计划，可按 hall、pool、channel 与左闭右开的接触日期筛选；regular 为例行，prior_loss 仅在上一名册一位或多位抽中者因失去许可而确认撤回后触发。`batch_members` 记录实际抽取并送出的成员，送出日不能用于回填尚未发生的接触。`batch_messages` 的 receipt 是接收方回执号、delivery 是每次投递号。`audit_capacity` 是每厅、渠道、日期范围可承担的总接触数，已联系也消耗该时段份额。授权更正保留发生日和收到日；`permission_corrections` 指向原限制，修正的是生效日而不是原抽取日期。
