# xihe\-agent × 企业微信集成方案（调研 \+ 设计）

> 部分内容由豆包生成
> 
> 

# 执行摘要

**核心结论**：企微集成的三条线（消息卡片化、办公能力接入、桌面端扫码向导）**可以并行推进、共享同一对机器人凭证**，现有 WS 长连接协议栈无需迁移；建议按 P0（审批卡片）→ P1（更新卡片）→ P2（clarify 卡片）→ P3（CLI/MCP 办公能力）顺序落地。

**依据**：

- 企微智能机器人长连接协议**原生支持 template\_card 卡片消息**与 aibot\_event\_callback 事件回调，与 xihe 现用的 markdown 通道同级，零协议迁移成本；

- xihe 已具备 MCP（stdio / streamable\-http）与 skills（SKILL\.md \+ frontmatter）接入面，wecom\-cli 官方与社区生态可直接复用；

- 桌面端（Electron）天然具备渲染二维码、打开授权页、状态联动能力，是「扫码建 bot」的最佳宿主；

- 凭证体系单一：消息通道与办公能力共用同一对智能机器人凭证，一次授权两处就绪。

**约束与风险**：

- 卡片能力要求企微客户端 **v4\.1\.39\+**，旧版本需降级为纯文本；

- xihe config\.yaml 中 secret 为明文存储，CLI 侧凭证为 AES\-256\-GCM 加密落盘，两套存储策略需在文档中说明；

- wecom\-cli 本体为单凭证模型，多机器人场景需走 MCP 多实例或物理隔离。

**行动**：批准 P0–P1（审批卡片 \+ 更新卡片）先行实施；桌面端向导与 CLI 接入作为独立工作项并行评估。

# 现状：企微通道与审批/clarify 实现

xihe 的企微接入位于 `src/gateway/platforms/wecom.py`（约 1150 行），走企业微信智能机器人 WebSocket 长连接协议（`wss://openws.work.weixin.qq.com`），命令集为 aibot\_subscribe / aibot\_msg\_callback / aibot\_send\_msg / aibot\_respond\_msg / ping。

|环节|实现位置|现状|
|---|---|---|
|**审批请求**|gateway/bot\.py \_approval\_request\_cb|纯文本「⚠️ 危险操作待确认…回复 y 批准 / n 拒绝 / a 不再询问」→ markdown 发送|
|**审批结果**|\_approval\_result\_cb|再发一条 ✅ 已批准 / ❌ 未批准 文本|
|**clarify**|tools/clarify\_tool\.py|返回结构化 \{question, options\} 给模型，由模型把问题写进回复，走普通 markdown|
|**发送通道**|wecom\.py send\(\)|固定 msgtype=markdown（CMD\_SEND / CMD\_RESPONSE）|
|**接收通道**|wecom\.py \_dispatch|仅处理 aibot\_msg\_callback（文本/图片/语音/文件），无事件回调分支|

来源：xihe\-agent 源码（src/gateway/platforms/wecom\.py、src/gateway/bot\.py、src/tools/clarify\_tool\.py）｜期间：2026\-09 核查

**关键缺口**：发送侧只有文本能力、接收侧没有事件通道——这正是卡片化的两个硬前提。

# 企微能力调研结论

## 卡片消息接口（长连接协议）

|能力|结论|
|---|---|
|**消息类型**|长连接智能机器人支持 template\_card（模板卡片）、markdown、file、image、voice、video；卡片与 markdown 同级，body\.msgtype 切换即可|
|**卡片类型**|五种：text\_notice（文本通知）、news\_notice（图文展示）、button\_interaction（按钮交互）、vote\_interaction（投票选择）、multiple\_interaction（多项选择）|
|**按钮交互**|用户点击按钮触发 aibot\_event\_callback（SDK 中为 template\_card\_event），携带 event\_key（按钮 id）、task\_id、response\_code|
|**更新卡片**|response\_code 72 小时内有效、仅可用一次，用于把卡片替换为结果文案（如审批后按钮区变灰）|
|**版本要求**|企微客户端 v4\.1\.39\+ 支持五类卡片；旧版本需降级纯文本|

来源：企微长连接智能机器人接口文档（docid=21657）、模板卡片事件格式（doc\-417806）、智能机器人能力说明（docid=21663）｜期间：2026\-09

## AI 开放能力

|能力|结论|
|---|---|
|**AI 开放能力**|企微开发者中心面向 AI 场景开放接口，支持 CLI 与 MCP 两种接入方式|
|**企微 CLI**|2026\-03\-30 开源（@wecom/cli，Rust 核心 \+ Node 包装），2026\-08 升级为不限企业规模、十大办公能力全开放|
|**API 模式机器人办公能力**|文档/表格/智能表格/智能文档创建与读取（\>10 人企业）；2026\-07\-02 起新增待办 API|
|**用户反馈机制**|用户可对机器人回复点赞/点踩，管理员可查日志|
|**长连接接入**|官方推荐方式，无需域名/IP，即 xihe 现用接入方式，未被废弃|

来源：企微开发者中心首页、企微 CLI 开源公告（央广网/新浪科技 2026\-03\-30）、InfoQ 能力升级（2026\-08\-18）、docid=21668｜期间：2026\-09

**协议层结论**：xihe 使用的 WS 长连接 \+ aibot\_\* 命令仍是官方当前推荐接入方式，无需迁移；需要补充的是「卡片发送」与「事件回调接收」两块能力。

# 卡片化改造方案

## 改造前消息流转与改造后对比

## 改动点清单

|优先级|改动|位置|改动量|
|---|---|---|---|
|P0|**send\_card\(\)**：msgtype=template\_card 发送|wecom\.py|约 30 行|
|P0|**事件回调接收**：新增 CMD\_EVENT\_CALLBACK 分支，解析 event\_key|wecom\.py \_dispatch / \_on\_event\_callback|约 40 行|
|P0|**审批改按钮交互卡**：button\_interaction \+ task\_id|bot\.py \_approval\_request\_cb|约 30 行|
|P1|**更新卡片**：response\_code 替换结果文案|wecom\.py \+ bot\.py|约 30 行|
|P2|**clarify 投票卡**：vote\_interaction 渲染选项|clarify\_tool\.py \+ bot\.py|约 80 行|
|P3|**企微 CLI/MCP 办公能力**|新功能接入|见下一章|

## 核心技巧：event\_key 直接映射裁决

卡片按钮回调的 event\_key（y / n / a）直接转成普通文本注入现有 MessageEvent 通道，approvals\.py 的 resolve\_pending\_reply\(\)、会话队列与 steer 逻辑**一行不用改**。

```python
CMD_EVENT_CALLBACK = "aibot_event_callback"

async def _on_event_callback(self, payload: dict):
    body = payload.get("body") or {}
    if body.get("event_type") != "template_card_event":
        return
    event_key = str(body.get("event_key") or "")
    # 复用审批裁决：resolve_pending_reply("wecom", chat_id, event_key)
    event = MessageEvent(text=event_key, chat_id=body.get("chatid"), msg_id=...)
    await self._emit_message(event)
```

```python
card = {
    "card_type": "button_interaction",
    "source": {"desc": "xihe-agent"},
    "main_title": {"title": "⚠️ 危险操作待确认", "desc": info.get("summary", "")},
    "button_selection": {
        "question_key": "approval",
        "title": "请选择操作",
        "option_list": [
            {"id": "y", "text": "批准"},
            {"id": "n", "text": "拒绝"},
            {"id": "a", "text": "本会话不再询问"},
        ],
    },
    "task_id": f"approval_{approval_id}",
}
await platform_adapter.send_card(event.chat_id, card, reply_to_msg_id=event.msg_id)
```

**降级策略**：卡片发送失败（旧客户端不支持）时 fallback 回现有纯文本 y/n/a 逻辑，原 send\(\) 不删。

# 企微 CLI 能力适配

## wecom\-cli 事实（v1\.3\.0 实测）

|项|内容|
|---|---|
|**形态**|npm 包 @wecom/cli（Rust 核心 \+ Node 包装，跨平台 x64/arm64），子命令 \+ JSON 参数：wecom\-cli todo get\_todo\_list '\{\}'|
|**安装**|npm install \-g @wecom/cli；官方 skill 包 npx skills add WeComTeam/wecom\-cli \-y \-g|
|**认证**|wecom\-cli auth init：默认扫码；\-\-manual 手动输 Bot ID/Secret；\-\-noninteractive \-\-output\-qrcode 适合无头环境|
|**凭证存储**|AES\-256\-GCM 加密落盘：Linux/macOS \~/\.config/wecom/，Windows %USERPROFILE%\\\.config\\wecom\\；auth show 查看状态|
|**能力域**|13 个服务：calendar / chat / contact / disk / doc / mail / media / message / meeting / sheet / smartpage / smartsheet / todo|

来源：npm @wecom/cli（v1\.3\.0）、OSCHINA 项目页、沙箱实测 auth \-\-help 与能力列表｜期间：2026\-09\-21

## 方案 A：MCP 接入（推荐）

xihe 的 mcp\_servers 原生支持 stdio 与 streamable\-http（mcp\_tool\.py），配置即接入，工具名/参数/描述经 MCP 协议注入，模型开箱即用。

```yaml
mcp_servers:
  wecom:
    type: stdio
    command: "npx"
    args: ["-y", "@wecom/mcp-server"]
    env:
      WECOM_BOT_ID: "xxx"      # 显式 env 才会透传给子进程
      WECOM_SECRET: "xxx"
```

## 方案 B：CLI \+ 官方 Skill 包（轻量备选）

xihe 的 skills 机制为 SKILL\.md \+ YAML frontmatter，与官方 skill 包格式同源；把 WeComTeam/wecom\-cli 的 skill 目录拷入 xihe 用户 skills 目录，agent 经 terminal 工具执行 CLI。优点零开发，缺点依赖 terminal 工具集、执行粒度较粗。

## 凭证管理与多机器人

|场景|做法|
|---|---|
|**单机器人**|方案 B 足够：auth init 一次加密落盘，凭证不进 xihe config，无泄漏面|
|**多机器人**|CLI 本体为单凭证模型（无 profile 切换）；首选方案 A MCP 多实例（xihe mcp\_servers 配多个名字，工具天然隔离），或物理隔离（每机器/每 HOME 一套），或官方 OpenClaw 插件 accounts 多账号机制|
|**凭证双写**|同一对凭证可同时写 CLI 加密文件（办公能力）与 xihe config platforms\.wecom（消息通道），一次授权两处就绪|

# 桌面端「企微集成」扫码向导

xihe gateway 的 wecom 适配器启动即要求 bot\_id \+ secret（缺一不可），「先有 bot」是硬前置。扫码交互适合放在桌面端完成，gateway 保持无头兼容。

## 向导四步

1. **检测**：auth show 未授权或 config 无 bot\_id → 显示「接入企微」入口；

2. **扫码**：spawn wecom\-cli auth init \-\-noninteractive \-\-output\-qrcode，渲染二维码卡片并打开授权链接；

3. **验证**：手机企微扫码确认，轮询 auth show 至授权成功，读回凭证；

4. **双写 \+ 重启**：写 CLI 加密凭证（CLI 自动完成）\+ 写 config platforms\.wecom → 重启 serve 就绪；扫码失败走 \-\-manual 手动粘贴兜底。

**gateway 兼容性**：无头环境用 \-\-output\-qrcode 输出 PNG \+ \-\-manual 两条路均可用，桌面端只是多包一层引导层。

# 路线图与实施建议

|阶段|内容|改动量|收益|
|---|---|---|---|
|**P0**|send\_card \+ 事件回调 \+ 审批按钮交互卡|约 100 行|审批体验质变，闭环可验证|
|**P1**|response\_code 更新卡片（结果变灰）|约 30 行|单消息闭环，状态一目了然|
|**P2**|clarify 投票卡（需动工具协议）|约 80 行|澄清交互轻量化|
|**P3**|CLI/MCP 办公能力接入 \+ 桌面端扫码向导|新功能|agent 操作企微办公套件|

**建议**：P0 优先落地并随下个 Release 发布；P3 的桌面端向导与 CLI 接入可并行设计，凭证体系保持单一来源。

# 附录：信息来源

1. 企微长连接智能机器人接口文档：open\.work\.weixin\.qq\.com（docid=21657，消息类型含 template\_card）

2. 企微模板卡片事件格式：qiyeweixin\.apifox\.cn（doc\-417806：event\_key / task\_id / response\_code）

3. 企微智能机器人能力说明：open\.work\.weixin\.qq\.com（docid=21663，v4\.1\.39\+ 五类卡片）

4. 企微 API 能力说明：open\.work\.weixin\.qq\.com（docid=21668，文档/待办 API）

5. 企微 CLI 开源公告：央广网科技 / 新浪科技（2026\-03\-30）

6. 企微 CLI 能力升级：InfoQ 写作社区（2026\-08\-18，十大办公能力）

7. @wecom/cli npm 包（v1\.3\.0）与官方 skill 包 WeComTeam/wecom\-cli

8. wecom\-mcp\-server 社区封装（LobeHub MCP 目录）[
]()
9. xihe\-agent 源码（wecom\.py / bot\.py / clarify\_tool\.py / mcp\_tool\.py / config\.example\.yaml）

> （注：部分内容可能由 AI 生成）
