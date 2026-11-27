# -*- coding: utf-8 -*-
"""
助手逐条语义理解的金标判定（240 条，覆盖 12 款游戏）。
每条: review_id -> [(aspect, sentiment), ...]
sentiment: positive / negative / neutral
判定原则：只标评论中"明确表达"的主题情感；反讽按字面情感的目标判定；
          一句多主题时分别标注；utral 仅用于客观陈述无明确情感倾向。
"""
GOLD = {
# ---------- CS2 ----------
236044561: [("content","positive"),("multiplayer","negative")],  # 好玩但炸鱼
160910192: [("content","positive"),("cheating","negative")],  # 好玩 挂满天飞
202111793: [("cheating","negative"),("monetization","negative"),("multiplayer","negative")],  # 挂不管 开箱坑 匹配
232351899: [("controls","positive"),("performance","negative")],  # 手感好 卡 帧数低
187449360: [("content","positive"),("cheating","negative")],  # 玩法棒 但wg多
186536185: [("cheating","negative"),("community","negative")],  # 开挂不管 管理层
221057071: [("graphics","positive"),("controls","positive"),("cheating","positive"),("performance","negative")],  # 画面重塑 手感硬核 公平 优化波动
216381110: [("content","positive"),("multiplayer","positive")],  # 经典好玩 队友残局有趣
167991944: [("content","positive")],  # 好玩 佳作 上瘾
208037585: [("content","positive"),("monetization","neutral")],  # 好玩 买皮肤梗
149379907: [("cheating","negative"),("monetization","negative")],  # 挂多 被举报 冤枉钱
202569749: [],  # 无害玩梗
153697547: [("cheating","negative"),("controls","negative"),("multiplayer","negative"),("community","negative")],  # 误封 手感 服务器 延迟
152639553: [("performance","negative")],  # 帧数低不稳
204163020: [("multiplayer","negative"),("monetization","negative"),("content","negative")],  # 匹配慢 掉线 卖箱子 宠物系统
158701347: [("audio","negative")],  # 爆头音效不能调
149933470: [("community","negative"),("multiplayer","negative"),("cheating","negative"),("performance","negative")],  # V社 服务器 外挂 优化
207381950: [("monetization","negative")],  # 饰品市场 炼金讽刺
166318970: [("performance","negative")],  # 负优化 bug
217616460: [("performance","negative"),("controls","negative"),("community","negative")],  # 垃圾优化 手感 策划
# ---------- DOTA2 ----------
230739159: [("multiplayer","negative")],  # 匹配 智障队友
12432928: [("content","positive"),("monetization","negative")],  # 快乐 开不出东西
162190346: [("multiplayer","negative")],  # 匹配机制
128842647: [("community","negative"),("monetization","negative")],  # 垃圾V社 掉落
49278283: [("multiplayer","negative"),("community","negative")],  # 服务器烂
96411999: [("multiplayer","negative"),("content","negative")],  # 不能开黑 惩罚
239002952: [("multiplayer","negative")],  # 炸鱼 elo
144895705: [("multiplayer","negative"),("community","negative")],  # 重连摆设 玩家戾气
75793465: [("community","negative"),("multiplayer","negative")],  # 玩家素质 开喷
34948930: [("multiplayer","negative"),("community","negative")],  # 连不上服务器 运营商
147172359: [("community","negative"),("monetization","negative")],  # 画饼 本子坑钱
59524223: [("performance","negative")],  # BUG
88519850: [("content","positive"),("community","neutral")],  # 电竞观赏性 版本抛弃
83879315: [("multiplayer","negative"),("community","negative")],  # 队友 喷
63272414: [("graphics","positive")],  # 画面不错
72761563: [("community","negative"),("multiplayer","negative")],  # 环境恶劣 素质低
105697638: [("multiplayer","negative")],  # 队友 节奏慢
138325829: [("content","positive"),("controls","negative")],  # 比LOL好玩 难度高
40826567: [("community","neutral")],  # plus会员建议
50620752: [("community","negative")],  # 傻逼V社 心寒
# ---------- GTA5 ----------
191298471: [("cheating","negative"),("content","positive")],  # 反外挂差 好玩爱玩
127850172: [("content","positive"),("cheating","negative")],  # 好玩 外挂多
140258218: [("content","negative"),("controls","negative")],  # 载具削弱
153702614: [("level_design","positive"),("story","positive")],  # 精致开放世界 文化精髓
124213489: [("cheating","negative"),("performance","negative"),("multiplayer","negative")],  # 挂 等待 被踢
149069963: [("story","positive"),("content","positive")],  # 剧情情怀 R星梦
176498174: [("content","positive"),("story","positive")],  # 罪城回忆
189431776: [("community","negative")],  # 线上运营
230690850: [("community","negative")],  # 傻逼运营
125129705: [("multiplayer","negative")],  # 土豆服务器
124157406: [("cheating","negative"),("community","negative")],  # 挂不封 误封
130703214: [("cheating","negative")],  # 警察和挂逼
147926419: [],  # 英文 无明确主题
158809686: [("monetization","negative")],  # 豪华版礼包没发
153635632: [("cheating","negative"),("community","negative")],  # 封号机制 举报不封
129659102: [("cheating","negative")],  # 开挂无敌
176794291: [("cheating","negative"),("content","positive")],  # 虽有外挂 但实现童年愿望
136990421: [("performance","negative"),("graphics","negative")],  # 帧数 画质糊
187447633: [("performance","negative"),("controls","negative"),("graphics","negative"),("level_design","negative"),("community","negative")],  # 启动慢 操控差 建模糙 引导少
182463374: [("cheating","negative"),("performance","negative")],  # battleeye 踢人
# ---------- Garry's Mod ----------
24515461: [("content","positive"),("monetization","positive")],  # 模组省钱
18406456: [("performance","positive"),("content","positive")],  # 配置低 模式多
151238254: [("content","positive")],  # 玩法多样
189492078: [("graphics","negative"),("content","positive")],  # 画面不如 童年
202258675: [("content","positive")],  # 好玩 有趣
140501148: [("multiplayer","negative")],  # 爆ping
111278321: [("content","positive"),("level_design","positive")],  # 潜力 半衰期地图
30159930: [("content","positive")],  # 物理枪 推荐
39201845: [("content","positive"),("multiplayer","neutral")],  # 爽 联机
16179868: [("content","positive"),("monetization","positive")],  # 沙盘 价格实惠
127739539: [("content","positive")],  # 内容丰富 mod
35967412: [("multiplayer","positive"),("content","positive")],  # 基友开黑 模式好玩
20946268: [("controls","negative")],  # 入门难度高
97103082: [("content","positive"),("multiplayer","negative")],  # 好玩但没朋友
36568641: [("level_design","positive"),("content","positive")],  # 地图多 玩不腻
238656526: [],  # DOTA梗诗 无明确主题
155137701: [("graphics","negative"),("content","negative")],  # 垃圾 画质清朝
48876866: [("content","positive")],  # 百玩不腻
137549139: [("content","positive"),("multiplayer","positive")],  # 可玩性高 服务器
89542866: [("content","positive"),("multiplayer","negative")],  # 经典 老服务器没了
# ---------- Rust(腐蚀) ----------
25082086: [("content","positive"),("graphics","positive"),("cheating","negative"),("multiplayer","positive")],  # 画面内容不错 作弊多 小伙伴乐趣
95991606: [("cheating","negative"),("community","negative")],  # 挂多 腐竹不ban
20488523: [("content","positive"),("multiplayer","positive")],  # 可玩性 团队游戏
168103379: [("multiplayer","negative"),("community","negative")],  # 官服歧视 封人
139006321: [("content","positive"),("controls","negative")],  # 可玩性高 新手不友好
90409378: [("cheating","negative"),("community","negative"),("multiplayer","negative")],  # 瞎封 全是挂 退坑
137064369: [("community","negative")],  # 挖矿被封
213630267: [("multiplayer","negative"),("monetization","negative")],  # 服务器ban 给会员让位
22744677: [("performance","negative"),("graphics","negative"),("community","negative")],  # 优化烂 卡 胶水
52765263: [("content","positive"),("cheating","negative")],  # 好玩 外挂多
32964025: [("multiplayer","negative"),("content","negative")],  # 延迟高 玩法单一
98650410: [("community","negative")],  # 平白封号
208868463: [("controls","negative"),("performance","negative")],  # 操作设置改不了 F13
31681851: [("controls","positive"),("content","positive")],  # 上手度高 挺好
81016193: [("content","positive"),("multiplayer","negative")],  # 最爱 人少了
45486612: [("content","positive"),("controls","positive")],  # 难度游戏 值得推荐
39415543: [("cheating","negative")],  # 被挂恶心
32704720: [("content","positive"),("cheating","negative"),("controls","negative")],  # 不错 外挂多 难上手
8907998: [("multiplayer","negative"),("controls","negative"),("content","positive")],  # 瞬移ping 动作僵硬 有潜力
113828133: [("content","positive"),("multiplayer","positive")],  # 科技树合作好玩
# ---------- 军团要塞2 ----------
14557605: [("content","positive"),("multiplayer","positive"),("monetization","positive")],  # 好玩 合作 交易
11777758: [("content","positive")],  # 耐玩 战略性
63515806: [("cheating","positive"),("monetization","positive")],  # 平衡性高 比人民币游戏好
73467442: [("cheating","negative")],  # 外挂
93940703: [("cheating","negative")],  # 讽刺作弊 第三方伺服
20764563: [("monetization","positive"),("content","positive")],  # F2P不P2W (英文)
33393799: [("cheating","negative")],  # 讽刺作弊
33210959: [("cheating","negative")],  # 讽刺作弊(与93940703重复)
175444516: [("multiplayer","negative")],  # 老手新手匹配 没体验
14223845: [("content","positive"),("community","positive")],  # 有意思 良心制作
132308480: [("content","positive"),("audio","negative")],  # 好玩 玩家放音乐影响操作
21181548: [("monetization","positive")],  # 免费推荐
44915285: [("content","positive")],  # 耐玩
45131933: [("graphics","negative"),("content","negative")],  # 画风不喜欢 不如OW
38094500: [("multiplayer","neutral"),("controls","neutral"),("cheating","neutral"),("content","positive")],  # 人数不平衡 手感 刺激
61789323: [("content","positive"),("level_design","positive")],  # 独特玩法 最好玩
81577109: [("level_design","positive"),("cheating","positive"),("monetization","positive"),("graphics","neutral"),("content","positive"),("audio","neutral")],  # 全维度好评
34732207: [("content","positive"),("multiplayer","positive"),("graphics","neutral"),("story","neutral")],  # 童年 高可玩性 社群
8850161: [("content","positive")],  # best FPS
42903546: [("content","negative"),("monetization","negative")],  # 皮肤 不出武器
# ---------- 彩虹六号 ----------
33897874: [("content","positive"),("multiplayer","negative")],  # 更新好 队友坑
42739290: [("community","negative"),("content","negative")],  # 新赛季阴间
161798229: [("content","positive"),("level_design","positive"),("multiplayer","negative"),("cheating","negative"),("community","negative")],  # 地图精妙 服务器土豆 育碧
65405850: [("controls","neutral")],  # 盾牌腰射
55166471: [("community","positive"),("monetization","positive")],  # 育碧牛逼 精英皮肤
44206768: [("cheating","negative"),("multiplayer","negative")],  # 挂多 匹配机制
39340206: [("content","positive")],  # 耐玩 玩法多
33298663: [("monetization","negative"),("multiplayer","negative")],  # 别买 土豆服务器
42029245: [("multiplayer","negative")],  # 连不上服务器
122227754: [("level_design","positive"),("content","positive")],  # 地图理解 好玩
66594532: [("cheating","positive"),("level_design","negative"),("content","positive")],  # 公平好 翻新地图打击
35531787: [("controls","negative"),("performance","negative")],  # 萌新不友好 吃配置
46354563: [("content","positive"),("multiplayer","negative"),("community","negative")],  # 立体进攻 服务器烂
124635318: [("multiplayer","negative")],  # 服务器连不上
22879541: [("cheating","negative"),("community","negative")],  # 不严打外挂
47609090: [("monetization","negative")],  # 太贵 太肝
43182641: [("content","positive"),("multiplayer","positive"),("cheating","negative"),("community","negative"),("controls","negative")],  # 配合好 外挂多 盾牌捞
32838597: [("community","negative"),("cheating","neutral")],  # 强退玩家
65551835: [("graphics","positive"),("content","positive")],  # 画质好 玩法新颖
87529085: [("cheating","negative"),("content","negative"),("community","negative")],  # 平衡烂 不出新
49764811: [],  # 转卖钩子
# ---------- 星露谷物语 ----------
142377661: [("content","positive"),("audio","positive"),("multiplayer","positive")],  # mod bgm 联机 种植养成 好玩
30357837: [("content","positive"),("graphics","neutral")],  # 像素 玩法乐趣
30120942: [("performance","negative"),("community","positive")],  # 崩溃bug 中文方便
24383573: [("performance","negative")],  # 闪退
194058294: [("content","positive"),("graphics","neutral")],  # 好玩 画质无关
115233589: [("story","positive"),("content","positive"),("level_design","neutral")],  # 村民故事 姜岛
53597190: [("content","positive"),("multiplayer","positive")],  # 肝 和朋友开档
198425528: [("content","positive"),("community","positive")],  # 好玩 人友善放松
180355505: [("content","positive"),("community","positive"),("monetization","positive")],  # 免费更新 感谢作者
152381670: [("content","positive"),("level_design","neutral")],  # 好玩 姜岛
136187491: [("content","positive")],  # 上头
59110669: [("content","positive"),("multiplayer","negative")],  # 休闲 联机不够好
145580562: [("story","positive"),("content","positive"),("level_design","neutral")],  # 回忆 姜岛
130925525: [("content","positive"),("level_design","neutral")],  # 姜岛
92950150: [("story","neutral")],  # 吐槽丈夫 剧情
165864642: [("audio","positive"),("content","positive")],  # 音乐 舒适
27388670: [("content","positive"),("content","negative")],  # 不错 但后期可玩性低 (重复aspect取后)
215818699: [("content","positive")],  # 好玩 可玩性高
240275220: [("content","positive")],  # 好耍
157191944: [("story","neutral")],  # 剧情心理探讨
# ---------- 求生之路2 ----------
61217369: [("content","positive"),("graphics","neutral")],  # 好玩 高清包
199916948: [("monetization","positive"),("multiplayer","positive"),("level_design","positive")],  # 促销 合作 地图
168680532: [("content","positive"),("multiplayer","positive")],  # 神中神 联机快乐
71718525: [("content","positive"),("monetization","positive"),("community","positive")],  # MOD 打折 V社
205933954: [("performance","positive"),("monetization","positive"),("content","neutral")],  # 配置低 打折
75094269: [("performance","positive"),("content","positive")],  # 神优化 经典
21425829: [("monetization","positive"),("level_design","positive"),("content","positive")],  # 价格良心 自定义地图 耐玩
30828310: [("multiplayer","positive"),("level_design","neutral"),("monetization","neutral")],  # 开黑有趣
69171519: [("multiplayer","neutral")],  # 梗
39108033: [("multiplayer","positive")],  # 开黑有意思
148679604: [("content","positive"),("multiplayer","neutral")],  # 经典 AI队友
12288591: [("content","positive"),("audio","neutral"),("graphics","neutral"),("multiplayer","positive")],  # 推荐 卖队友
96803151: [("content","positive"),("performance","positive")],  # 模组 小容量
29291629: [("multiplayer","positive"),("monetization","positive")],  # 朋友快乐 不氪金
13633326: [("content","positive"),("monetization","positive")],  # 近战好评 V社大包
132094770: [("content","positive"),("monetization","positive")],  # 好玩 值
218695367: [("multiplayer","positive"),("content","positive")],  # 开黑 喜欢
77286282: [("content","negative")],  # 更新跟没更新一样
18956944: [("content","positive"),("community","negative")],  # 欢乐 下载限速
# ---------- 泰拉瑞亚 ----------
215271050: [("multiplayer","positive"),("controls","negative")],  # 朋友玩有意思 上手门槛
110976948: [("content","positive"),("community","positive"),("controls","positive")],  # 武器彩蛋 教程 boss挑战
145392121: [("content","positive"),("multiplayer","positive"),("community","positive")],  # 团建 内容丰富 一直更新
95219936: [("multiplayer","positive"),("content","positive")],  # 开黑快乐 玩不腻
115325891: [("content","positive"),("multiplayer","positive"),("controls","positive")],  # 好玩 多人 boss
64862879: [("content","positive"),("graphics","neutral")],  # BOSSrush爽
73814695: [("content","positive")],  # 内容史诗
71861171: [("content","positive"),("level_design","positive")],  # 自由度 开放
41559187: [("content","negative"),("monetization","neutral")],  # 初始枯燥 想加mod
130550486: [("content","positive"),("controls","positive"),("level_design","positive")],  # 战斗 探索建造
213515832: [("multiplayer","positive"),("content","positive"),("graphics","positive")],  # 一起玩 大师模式 烟花
146218894: [("graphics","negative"),("multiplayer","negative")],  # 画质低 不能开麦
197783275: [("content","positive"),("controls","negative")],  # 内容丰富 缺新手指导
21518572: [("content","positive"),("multiplayer","positive"),("graphics","neutral")],  # 战斗收集 朋友玩
58550964: [("content","positive"),("multiplayer","neutral")],  # 好玩 找人玩
37057109: [("content","positive"),("multiplayer","positive")],  # 入迷 小伙伴
64373707: [("content","positive"),("monetization","positive"),("multiplayer","positive")],  # 丰富 正版方便 联机
217987050: [("content","positive"),("community","positive"),("controls","positive")],  # 更新提升 成长曲线
116363849: [("content","positive"),("audio","positive"),("graphics","positive"),("controls","positive")],  # 音乐 画面 大师模式
146594475: [("content","positive"),("multiplayer","positive")],  # 好玩 双人开荒
# ---------- 艾尔登法环 ----------
174533496: [("controls","negative"),("content","negative")],  # 设置锁定翻滚 boss套用
170220801: [("content","positive"),("level_design","negative")],  # 喜欢 dlc地图空
154263876: [("content","positive"),("performance","negative")],  # 游戏好 PC优化差
117877601: [("content","positive"),("performance","positive"),("level_design","positive")],  # 优化负面体验 新发现
116140743: [("content","neutral"),("controls","neutral")],  # 催DLC
130696876: [("community","negative")],  # 误封
168255351: [("content","neutral"),("community","negative")],  # 封号
215450769: [("level_design","positive"),("audio","positive"),("graphics","positive")],  # 开放世界 音乐美术 场景
171286598: [("story","positive"),("content","positive"),("graphics","positive")],  # 碎片叙事 沉浸
213095281: [("controls","negative"),("level_design","negative")],  # 战斗系统讽刺 堆怪
136577750: [("content","positive"),("story","positive"),("level_design","positive"),("graphics","positive"),("controls","positive")],  # 无可挑剔
134071164: [("level_design","positive"),("content","positive")],  # best open world
199789010: [("level_design","negative")],  # 毫无引导
189652835: [("level_design","negative"),("content","positive")],  # 地图阴间
168611529: [("content","positive"),("controls","negative"),("graphics","negative")],  # 本体耐玩 boss猛 墓碑刺眼
121565160: [("multiplayer","positive"),("content","positive")],  # 安利一起玩
117911220: [("content","positive"),("monetization","positive")],  # 可玩性高 值
172015866: [("performance","negative")],  # 闪退
135143478: [("multiplayer","negative"),("performance","negative"),("content","negative")],  # 强制PVP 卡
170864453: [("content","negative"),("multiplayer","positive")],  # DLC空 无缝联机mod
# ---------- 黑神话:悟空 ----------
183854005: [("story","positive"),("graphics","positive"),("controls","negative"),("content","positive")],  # 故事画面好 难
199196312: [("performance","negative"),("content","positive")],  # 优化差 什么都好
179058750: [("content","positive")],  # 豪玩推荐
175073829: [("controls","positive"),("graphics","positive"),("story","positive")],  # 战斗10分 美术9.5 故事9
191540995: [("content","positive"),("controls","positive")],  # 成就感 有意思
175364966: [("controls","positive"),("story","positive"),("content","positive")],  # 战斗故事 期待DLC
176021824: [("content","positive")],  # 好玩爱玩 等DLC
175109051: [("graphics","positive"),("content","positive")],  # 画质精美 喜欢
236982044: [("content","positive"),("graphics","positive"),("level_design","neutral")],  # 好看好玩 反驳空气墙
200721148: [("monetization","negative")],  # 明末豪华冤种
180652989: [("content","positive")],  # 好好好 电子阳痿
177980026: [("story","positive"),("content","positive")],  # 真结局 期待dlc
223348745: [("controls","negative"),("level_design","negative")],  # 操作系统大便 boss关多人
185013737: [("content","positive"),("multiplayer","negative")],  # 中国元素 没联机 中期无聊
174928542: [("graphics","positive"),("controls","positive"),("story","positive"),("content","positive")],  # 全10分
180498193: [("graphics","positive"),("controls","positive"),("level_design","negative")],  # 美术薄纱 地图吐槽
175172647: [("content","positive"),("controls","positive"),("story","positive"),("level_design","negative")],  # 好玩 难度合适 后三章地图差
179201579: [("controls","positive"),("graphics","positive"),("audio","positive"),("story","positive"),("content","positive")],  # 战斗美术音乐剧情神
182729326: [("graphics","negative")],  # 边缘太暗
180851248: [("content","neutral")],  # DLC吐槽
}
