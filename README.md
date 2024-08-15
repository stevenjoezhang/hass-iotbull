# Bull IoT for Home Assistant

[![通过HACS添加集成](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=stevenjoezhang&repository=hass-iotbull&category=integration)

本插件实现了公牛智家协议的规范，可将公牛智家设备自动接入[Home Assistant](https://www.home-assistant.io)。本插件能够控制开关、插座、窗帘，能够自动更新设备的在线状态，对于支持电量统计的设备能够显示实时功率。

目前测试已支持的公牛智家设备包括：

| globalProductId | nickName |
| - | - |
| 3 | G27网关插座(zigbee版) |
| 4 | G27智能一位开关(单火/zigbee) |
| 5 | G27智能二位开关(单火/zigbee) |
| 6 | G27智能三位开关(单火/zigbee) |
| 7 | G27智能二三极插座(10A) |
| 13 | WiFi智能转换器 |
| 14 | WiFi智能转换器(电量统计版) |
| 15 | G27智能一位无线开关(zigbee) |
| 20 | G27智能三位全屋开关(无线/zigbee) |
| 31 | 智能窗帘CW11/C035 |
| 33 | G55智能网关插座 |
| 34 | G55智能开关(一位零火) |
| 35 | G55智能开关(二位零火) |
| 36 | G55智能开关(三位零火) |

如果您使用的设备不在列表中，欢迎提交Issue或Pull Request。

本插件支持Home Assistant后台界面集成，无需编写yaml配置文件即可轻松将设备接入。

## 配置方式

首先在手机上下载「公牛智家」App，并按照提示将家中的公牛智家设备（包括开关和网关等）接入网络。如果您是使用手机验证码的方式登录的「公牛智家」App，还需要为账户设置密码，因为本插件目前只支持通过用户名和密码登录。

添加设备后，您可以在「公牛智家」App中设置好设备所处的房间（例如「客厅」「卧室」等）和设备名称（例如「吊灯」「灯带」等），本插件能够自动读取相关信息并显示在Home Assistant中。

在配置完成后，将本仓库安装到Home Assistant。具体方法是，先克隆这个仓库到部署Home Assistant的主机上：

```sh
git clone https://github.com/stevenjoezhang/hass-iotbull
```

然后，将其中的`custom_components/bull`子目录复制进Home Assistant的数据目录。例如，数据目录是`~/hass`，那么执行以下命令：

```sh
cp -r hass-iotbull/custom_components/bull ~/hass/custom_components
```

完成后，重启Home Assistant，在配置界面选择添加集成，搜索「Bull IoT」，按照提示操作即可。

## 隐私

本插件可能会收集您所使用的设备的`globalProductId`、`deviceInfoVo`和`property`信息。这些信息不包含任何隐私数据，它们对于所有购买了同款设备的用户都是相同的。这些数据仅用于帮助插件作者分析是否有新款设备加入，并对它们进行适配。

## 免责声明

请注意，对Home Assistant或本插件的使用可能带来安全风险。例如，通过Home Assistant自动化定时启动、关闭加热设备，可能因软硬件故障导致火灾风险。

本插件仅供研究学习使用。请您注意用电安全，本插件作者不对由使用该插件产生的任何后果负责。
