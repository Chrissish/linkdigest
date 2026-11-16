"""网络层：出口选择、HTTP 传输、探活、cookies、失败归因。

所有出网动作都经过这里，采集层不再自己 `httpx.get`。
为避免包级循环导入，这里只放说明，具体能力按下述模块导入：

    net.proxy       出口：当前值、候选、写入/清除环境变量、macOS 系统代理
    net.http        统一的 httpx 客户端与 GET 助手
    net.transfer    直链下载（带一次换出口重试）
    net.probe       reachability()：直连 → 代理 依次试
    net.cookies     Netscape cookies.txt 的发现、体检、真打站点验登录
    net.errors      DownloadFailure 与中文报错归因（文案在 resources/messages）
"""
