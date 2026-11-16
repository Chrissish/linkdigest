# 用登录态抓取（cookies）

多数平台不需要 cookies。需要它的只有两类：**平台按 IP 判定机器人**（YouTube 最常见），
和**内容本身要求登录**（抖音、小红书、B 站高清）。

LinkDigest 不内置账号体系，也不代你登录。它只读一份 Netscape 格式的 `cookies.txt`，
或让 yt-dlp 直接读浏览器 cookie 库。怎么把那份文件弄进来，就是你自己的选择。

> `data/cookies.txt` 是明文登录凭证，等价于你的账号密码。默认已被 `.gitignore` 排除，
> 不要提交、不要贴进 issue。

## 判断到底缺什么

报 `Sign in to confirm you're not a bot` 时，先分清是「网络不通」「没有登录态」还是「IP 被判」，
三件事的处理方式完全不同：

```bash
# 1. 网络能不能到（到得了 ≠ 不被判机器人）
python -m linkdigest.cli --check "https://www.youtube.com/watch?v=xxx"

# 2. 本地这份 cookies 算不算登录态（纯本地判断，秒出）
python -c "import sys,json; sys.path.insert(0,'src'); from linkdigest.net.cookies import summary as cookies_summary; \
print(json.dumps(cookies_summary(), ensure_ascii=False, indent=1))"

# 3. 服务端认不认（带 cookies 真打一次站点，读页面的 LOGGED_IN）
python -c "import sys,json; sys.path.insert(0,'src'); from linkdigest.net.cookies import login_state; \
print(json.dumps(login_state(), ensure_ascii=False, indent=1))"
```

判定标准写死在这里，因为踩过坑：

- **`__Secure-3PSID` 单独存在不算登录。** 未登录的 Chrome 访问 youtube.com 也会拿到它。
- YouTube 认的是 `SID / HSID / SSID / APISID / SAPISID / LOGIN_INFO` 这一套（HttpOnly）。
- `logged_in: false` 但 `google_session: true` 是最常见的坑：你是登录着 Google，
  但 youtube.com 名下没有认证 cookie，于是平台当你是访客。

## 三条获取路径

### A. 粘 Cookie 请求头（推荐，不需要插件，不需要磁盘权限）

```bash
# 浏览器登录 YouTube → 打开任意视频 → F12 → Network → 筛 youtubei
# → 选中一条请求 → Request Headers → 复制 cookie: 后面整串
pbpaste | ./scripts/cookies.sh
```

脚本做的事：解析请求头 → 写成 Netscape 格式 → **合并**进 `data/cookies.txt`（只替换
youtube / google 相关行，别的站点登录态保留，原文件备份成 `data/cookies.bak-<时间戳>.txt`）
→ 联网校验 `LOGGED_IN`。

只有打出 `✅ 服务端认这份登录态` 才算修好；否则退出码非 0，并指出缺哪几个 cookie。
最常见的失败原因是复制了不带认证 cookie 的请求——刷新页面后从 `youtubei/v1/player` 那条复制。

### B. 复用浏览器 cookie 库

```bash
./scripts/export_cookies.sh --doctor      # 先看是哪一层拦住了
./scripts/export_cookies.sh chrome        # 或 safari / edge，也支持 "chrome:Profile 1"
```

或者在 `.env` 里写 `COOKIES_FROM_BROWSER=chrome` 让 yt-dlp 自己读。

前提是**终端 App 有「完全磁盘访问权限」**（系统设置 → 隐私与安全性 → 完全磁盘访问权限）。
没有这个权限时，你会看到 `could not find chrome cookies database`——看起来像"浏览器没装"，
其实是 macOS 的 TCC 把整个目录挡住了。Chrome / Edge / Brave 都受此保护。
改完权限要 Cmd+Q 完全退出终端再打开，关窗口不算。

### C. 浏览器扩展导出

装 Get cookies.txt LOCALLY 一类的扩展，在**已登录的窗口**里导出，存到
`data/cookies.txt`，或用 `COOKIES_FILE=/path/to/cookies.txt` 指过去。

导出时要包含 HttpOnly cookie，否则拿到的是访客版文件——导完用上面第 2 条命令验一遍。

## 有登录态仍然报同一句话

那就是出口 IP 被标了，跟 cookies 没关系。平台（尤其 YouTube）对数据中心 IP 的判定与账号无关，
换 yt-dlp 的 `player_client`（tv / ios / android / mweb / web_safari …）也绕不过去，
因为我们测过的 7 种组合在受限 IP 上返回的是同一个错误。可行的只有：换住宅出口，或降低触发频率。

## 什么时候该放弃

平台明确要求登录、而你不愿意交出 cookies 时，别绕。用「上传文件」入口把音视频本地喂进去，
管道后半段完全一样，只是省了采集这一环。
