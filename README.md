# Bull IoT for Home Assistant

本插件实现了公牛智家协议的规范，可将公牛智家设备自动接入[Home Assistant](https://www.home-assistant.io)。目前测试已支持的公牛智家设备包括：

| globalProductId | nickName |
| - | - |
| 3 | G27网关插座(zigbee版) |
| 4 | G27智能一位开关(单火/zigbee) |
| 5 | G27智能二位开关(单火/zigbee) |
| 6 | G27智能三位开关(单火/zigbee) |
| 15 | G27智能一位无线开关(zigbee) |

插件支持Home Assistant后台界面集成，无需配置yaml即可轻松将设备接入。

## 配置方式

首先在手机上下载「公牛智家」App，并按照提示将家中的公牛智家设备（包括开关和网关等）接入网络。如果您是使用手机验证码的方式登录的「公牛智家」App，还需要为账户设置密码，因为本插件目前只支持通过用户名和密码登录。

在配置完成后，将本仓库安装到Home Assistant。具体方法是，先克隆这个仓库到部署Home Assistant的主机上：

```sh
git clone https://github.com/stevenjoezhang/hass-iotbull
```

然后，将其中的`custom_components/bull`子目录复制进Home Assistant的数据目录。例如，数据目录是`~/hass`，那么执行以下命令

```sh
cp -r hass-iotbull/custom_components/bull ~/hass/custom_components
```

完成后，重启Home Assistant，在配置界面选择添加集成，搜索「Bull IoT」，按照提示操作即可。
