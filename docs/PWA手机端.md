# PWA 手机端第一版

手机负责显示和操作，电脑／服务器负责采集、AI 调用和数据库。复用原查询页，
不改变模型、盘口视角、推荐和结算逻辑。本版没有安卓 APK，也不需要应用商店。

## 电脑端

正式程序为 `dist/football_analysis.exe`；`dist/football_analysis_test.exe` 用于后续测试。原来双击的电脑端流程不变，
默认只监听本机。电脑浏览器可验证 PWA，但手机不能使用电脑的 `localhost` 地址。

## 手机访问同一局域网

在程序所在目录打开 PowerShell，设置访问密码后启动：

```powershell
$secret = Read-Host "设置访问密码（至少12个字符）" -AsSecureString
$env:FOOTBALL_ACCESS_PASSWORD = [System.Net.NetworkCredential]::new("", $secret).Password
.\football_analysis.exe query --serve --host 0.0.0.0 --port 8765 --no-open
```

源码运行可改为 `python run.py query --serve --host 0.0.0.0 --port 8765 --no-open`。
用 `ipconfig` 查看电脑 Wi-Fi／以太网 IPv4 地址，手机连接同一网络后访问
`http://电脑IP:控制台显示的实际端口/`，输入密码登录。必要时自行确认 Windows
防火墙的专用网络规则；本版不会自动开防火墙或设置路由器端口转发。

**局域网 HTTP 只能浏览，不能完整安装 PWA，而且密码与数据没有传输加密。**
仅适合可信网络短时测试；正式手机使用请走下面的 HTTPS 方式，不要直接向公网
开放 HTTP 端口。服务结束后可运行 `Remove-Item Env:FOOTBALL_ACCESS_PASSWORD`。

## 安装型 PWA：HTTPS

需先准备自己控制的域名和可信 HTTPS 证书，或者能提供 HTTPS 的安全隧道。
不需要把 AI Key 放到手机；后台环境变量与设置文件仍保存在电脑／服务器。

推荐反向代理只连接本机后台。设置密码后运行：

```powershell
.\football_analysis.exe query --serve --host 127.0.0.1 --port 8765 --no-open --public-url https://你的域名
```

例如使用另行安装的 Caddy，配置根域名代理，不加子路径：

```caddyfile
football.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

将示例域名换成自己的真实域名，并确保 DNS、证书和代理访问正常。
`--public-url` 必须与手机实际打开的 HTTPS 根地址完全一致；它用于跨站请求校验
以及设置 Secure 登录 Cookie。该模式端口被占用时会拒绝启动，不会偷偷换端口。
不用信任外部传入的 `X-Forwarded-*` 头。不要让代理缓存任何动态响应。
公网部署还应在代理层配置限流与访问控制，及时更新系统和代理软件。

安卓 Chrome 打开 HTTPS 地址并登录后，可以使用页面出现的“安装到桌面”图标
或浏览器菜单“安装应用／添加到主屏幕”。是否出现安装图标由浏览器决定。
登录会话最长保留 7 天；退出登录、后台重启或会话过期后需要重新登录。

## 离线与数据

仅缓存应用图标、静态脚本和断网页。比赛页面、盘口、AI 分析、设置、备份、API
及 CSV 不放入 Service Worker 缓存，也不支持离线采集或离线 AI。
第一次成功访问并注册 Service Worker 后，断网或后台不可达时会显示连接提示。
断网页“重新连接”返回查询页；登录失效则回到登录页。
电脑需保持开机并运行后台，或把后台部署到持续运行的服务器。

手机登录具有现有查询页的完整操作权限，包括设置与备份恢复；密码只交给可信使用者。
登录失败达到 10 次／分钟会短暂限流，不使用外部 IP 头绕过限制。

## 开发验证

```powershell
python -m unittest discover -s tests -p test_pwa.py
python tools/build_pwa_icons.py
```

图标生成依赖 Pillow，仅用于开发；运行 exe 不需要 Pillow。PyInstaller spec
会把 PWA 资源打入 exe。首次版本需在实际安卓设备上补做安装和后台连接验收；
电脑浏览器的移动视口验证不等于真机验收。
