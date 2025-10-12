# MosHome (Bull IoT) for Home Assistant

[![通过HACS添加集成](https://my.home-assistant.io/badges/hacs_repository.svg)][hacs]

![](https://forthebadge.com/images/badges/made-with-python.svg)
![](https://forthebadge.com/images/badges/powered-by-electricity.svg)
![](https://forthebadge.com/images/badges/makes-people-smile.svg)

本插件实现了 MosHome（公牛智家）协议的规范，可将公牛智能设备自动接入[Home Assistant](https://www.home-assistant.io)。本插件能够控制开关、插座、窗帘、充电桩，能够自动更新设备的在线状态，对于支持电量统计的设备能够显示功率、电压、电流等数据。

目前测试已支持的公牛智能设备包括：

| globalProductId | productName |
| - | - |
| 4 | G27智能一位开关(单火/zigbee) |
| 5 | G27智能二位开关(单火/zigbee) |
| 6 | G27智能三位开关(单火/zigbee) |
| 7 | G27智能二三极插座(10A) |
| 13 | WiFi智能转换器 |
| 14 | WiFi智能转换器(电量统计版) |
| 30 | WiFi智能转换器(16A电量统计版) |
| 31 | 智能窗帘CW11/C035 |
| 34 | G55智能开关(一位零火) |
| 35 | G55智能开关(二位零火) |
| 36 | G55智能开关(三位零火) |
| 53 | 电源净化插座智联版 |
| 56 | 智能窗帘GDS015 |
| 75 | 7kW交流充电桩(风尚智联款) |
| 102 | G55智能开关(三位零火)_2022 |
| 103 | G55智能开关(二位零火)_2022 |
| 104 | G55智能开关(一位零火)_2022 |
| 141 | 21kW交流充电桩(风尚智联款) |
| 149 | C100智能屏网关(零火) |
| 157 | C100智能开关(一位零火) |
| 158 | C100智能开关(二位零火) |
| 159 | C100智能开关(三位零火) |
| 180 | 大师轨道插座至尊款 |
| 196 | 单相7kW充电桩(乐享款) |

如果在设置 Home Assistant 时，插件正确显示了设备数量，但设备无法正确识别和操作，说明您使用的设备不在列表中，欢迎提交 Issue 或 Pull Request。

本插件支持 Home Assistant 后台界面集成，无需编写 yaml 配置文件即可轻松将设备接入。

## 添加设备

首先在手机上下载「MosHome（公牛智家）」App，并按照提示将家中的公牛智能设备接入网络。如果您是通过手机验证码登录的 App，还需要为账户设置密码，因为本插件只支持通过用户名和密码登录。

添加设备后，您可以在 App 中设置好设备所处的房间（例如「客厅」「卧室」等）和设备名称（例如「吊灯」「灯带」等），本插件能够自动读取相关信息并显示在 Home Assistant 中。

## 安装插件

在将所有设备添加完成后，将本插件安装到 Home Assistant。

### 通过 HACS 安装

如果您使用 HACS，可以直接点击[安装链接][hacs]，然后按照提示操作即可。

### 手动安装

如果不使用 HACS，可以手动安装插件。具体方法是，先克隆这个仓库到部署 Home Assistant 的主机上：

```sh
git clone https://github.com/stevenjoezhang/hass-iotbull
```

然后，将其中的`custom_components/bull`子目录复制进 Home Assistant 的数据目录。例如，数据目录是`~/hass`，那么执行以下命令：

```sh
cp -r hass-iotbull/custom_components/bull ~/hass/custom_components
```

## 配置方式

安装完成后，重启 Home Assistant。待 Home Assistant 启动后，在「设置」菜单中点击「设备与服务」选项，在新界面中选择「添加集成」，搜索「Bull IoT」，按照提示操作即可。

## 隐私

本插件可能会收集您所使用的设备的`globalProductId`、`productName`和`property`信息。这些信息不包含任何隐私数据，它们对于所有购买了同款设备的用户都是相同的。这些数据仅用于帮助插件作者分析是否有新款设备加入，并对它们进行适配。

## 免责声明

请注意，对 Home Assistant 或本插件的使用可能带来安全风险。例如，通过 Home Assistant 自动化定时启动、关闭加热设备，可能因软硬件故障导致火灾风险。

本插件仅供研究学习使用。请您注意用电安全，本插件作者不对由使用该插件产生的任何后果负责。

[hacs]: https://my.home-assistant.io/redirect/hacs_repository/?owner=stevenjoezhang&repository=hass-iotbull&category=integration
