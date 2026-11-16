"""播客解析：绕过播放器，去源头拿文件。

关键事实：Spotify / Apple Podcasts 的网页本身永远给不出音轨。
Spotify 是 Widevine DRM 加密流，Apple 只有播放器壳，页面里连 mp3 地址都没有。
但播客生态里「平台只是分发渠道，RSS 才是源头」——同一集音频在节目自己的
feed 里有公开的 `<enclosure>` 直链。所以固定三级路线：

  1) 页面 → 元数据（标题 / 节目名 / 封面 / 时长）   oEmbed、iTunes Lookup，免密钥
  2) 元数据 → 反查原始 RSS                         iTunes Search API，免密钥
  3) RSS → enclosure 直链 → 下载

死角只有一个：Spotify 独家（Exclusive）节目没有公开 RSS，必须明确报错让人上传音频。
"""
