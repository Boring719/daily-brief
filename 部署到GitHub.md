# 把简报搬到 GitHub 上（免费、不用开电脑）

本地版要电脑开着才会跑。搬到 GitHub 的服务器上之后，每天 8:00 由 GitHub 自动执行，
你的电脑关不关都一样。而且 GitHub 的服务器在国外，**BBC、路透、纽约时报、半岛这些
国内打不开的源都能抓到**，内容会更全。

成本：0 元。GitHub Actions 对公开仓库免费，这个任务每次只跑 2 分钟左右。

---

## 一、准备

你需要：

1. 一个 GitHub 账号（没有就注册：<https://github.com/signup>，邮箱验证一下即可）
2. 已经拿到的两个密钥：DeepSeek 的 `sk-...`、Server酱 的 `SCT...`

---

## 二、建仓库

1. 登录 GitHub，点右上角 `+` → **New repository**
2. `Repository name` 填：`daily-brief`
3. 选 **Public**（公开仓库才能免费用 Pages；里面不含任何密钥，放心）
4. 下面的 `Add a README file` **不要勾**
5. 点 **Create repository**

---

## 三、上传代码

建好仓库后会看到一个空页面，点 **uploading an existing file**（或 `Add file` → `Upload files`）。

然后把你电脑上 `daily-brief` 文件夹里的这些**全部拖进去**：

```
app/              整个文件夹
web/              整个文件夹
.github/          整个文件夹（里面是定时任务配置）
settings.json
README.md
```

⚠️ **千万不要上传** `secrets.json`（里面有你的密钥）。其他文件（data、logs、reports、各种 .bat）都不用传。

拖完点页面下方的 **Commit changes**。

---

## 四、填两个密钥

1. 仓库页面 → **Settings**（顶部菜单）
2. 左边栏 → **Secrets and variables** → **Actions**
3. 点 **New repository secret**，添加两条：

| Name | Secret |
| --- | --- |
| `DEEPSEEK_API_KEY` | 你的 `sk-...` |
| `SCT_SENDKEY` | 你的 `SCT...` |

名字必须一模一样（全大写、下划线）。

---

## 五、先手动跑一次

1. 仓库顶部 → **Actions** 标签
2. 左边选「每日国际简报」，右边点 **Run workflow** → 绿色按钮
3. 等 1～2 分钟，点进这次运行，看 **build** 里的日志
4. 日志最后出现「完成：xx 条要闻」就成功了，微信也应该收到消息

如果报红，点开看哪一步失败，把红色那行发给我。

---

## 六、打开网页存档（可选，但推荐）

1. 仓库 → **Settings** → 左边 **Pages**
2. `Source` 选 **Deploy from a branch**
3. `Branch` 选 **gh-pages**，目录选 **/ (root)**，点 **Save**
4. 等 1 分钟，页面顶部会出现网址：`https://你的用户名.github.io/daily-brief/`

这个网址手机也能打开，往期都在。微信推送里也会自动带上这个链接。

---

## 七、确认定时生效

- 定时写的是 `0 0 * * *`（UTC 零点）= **北京时间早上 8:00**
- 第一次定时运行要等第二天；想验证可以先手动 Run 一次
- GitHub 的定时**不保证分秒不差**，高峰期可能晚几分钟到半小时，正常现象

---

## 八、和本地版的关系

两边都会推微信，会收到两条。建议**只留一个**：

- 用云端：把本地「开启每日自动.bat」关掉（任务管理器里结束 python 进程即可），
  以后只双击桌面上传…不需要了，网页直接看 GitHub 那个网址
- 用本地：别配 GitHub

---

## 九、常见问题

**Q：仓库是 Public，我的密钥会不会泄露？**
A：不会。密钥存在 GitHub 的 Secrets 里，加密保存，日志里也会自动打码。

**Q：会不会跑到一半没额度？**
A：公开仓库的 Actions 分钟数不限。DeepSeek 是几分钱一天，Server酱 免费版每天 5 条。

**Q：长时间不管会不会停？**
A：GitHub 会停掉"60 天没有任何活动"的仓库的定时任务。这个任务每天都会提交存档，
算活动，一般不会被停。真停了，去 Actions 页面点一次 Run workflow 就恢复。

**Q：想改生成时间？**
A：改 `.github/workflows/daily.yml` 里的 `cron`。换算成 UTC 再减 8 小时，例如北京时间
7:30 → `30 23 * * *`。
