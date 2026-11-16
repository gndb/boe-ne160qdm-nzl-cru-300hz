# BOE NE160QDM-NZL · 核显 300Hz CRU 配置

雷神 R16 内屏经 Intel UHD 输出的 2560×1600、300Hz 实验配置，提供 EDID 文件、时序参数和导入恢复说明。

> 2026-11-05 采集时，Windows 报告 300Hz，用户反馈可用。物理跳帧、长期运行及冷启动/唤醒尚未独立验证。这是单机实验记录，适用性需逐台确认。

Experimental Intel UHD 300Hz profile for the BOE NE160QDM-NZL panel in one THUNDEROBOT R16.

## 下载与使用

从 [Releases 下载配置](https://github.com/gndb/boe-ne160qdm-nzl-cru-300hz/releases/tag/profile-2026-11-05)，按[操作指南](outputs/手动参数与恢复.md)导入或手动填写。导入前先在 CRU 中导出自己的可用配置。

| 文件 | 用途 |
|---|---|
| [300Hz 配置 BIN](outputs/BOE0D55-current-300Hz-1298.880MHz.bin) | 512 字节 EDID 覆盖快照，另含 60、240、280、约 290.365Hz 记录 |
| [原生时序参考](inputs/BOE0D55-native-reference-384.bin) | 384 字节历史 EDID 缓存，含原生 240/300Hz；未做恢复验证 |
| [手动参数与恢复](outputs/手动参数与恢复.md) | 完整参数、导入步骤、恢复与显示检查 |
| [资料来源](outputs/资料来源与验证边界.md) | 同型号记录、时序原理和测试方法 |
| [校验值](outputs/SHA256SUMS.txt) / [参数快照](outputs/profile.json) | 文件哈希、环境、各档时序与验证状态 |

## 记录环境

- **设备：** THUNDEROBOT R16，i9-13900HX，BOE NE160QDM-NZL / BOE0D55。
- **输出：** Intel UHD Graphics，驱动 32.0.101.7092。
- **采集：** 2026-11-05 19:39:38 UTC。本仓库记录核显路径；独显直连配置单独管理。

## 与原生时序的对比

两组时序均为 2560×1600、300Hz；实验配置通过减少消隐降低像素时钟。

| 时序 | 水平 × 垂直总计 | 像素时钟 | 垂直消隐时间 |
|---|---:|---:|---:|
| 历史原生参考 | 2720 × 1700 | 1387.200MHz | 约 196.08µs |
| 本仓库实验配置 | 2640 × 1640 | 1298.880MHz | 约 81.30µs |

计算关系：`像素时钟 = 水平总计 × 垂直总计 × 刷新率`。完整前沿、同步宽度、后沿见[参数表](outputs/手动参数与恢复.md#手动填写)。

这组手动时序未经厂家认证，也不属于标准 CVT-RB2。约 1304MHz 是用户试验中观察到的可用门槛，尚不能确定为 Intel 硬件上限。

## 核验文件

下载或克隆完整仓库后运行：

```console
python analysis/validate_profile.py
```

验证器只读文件，检查 SHA-256、EDID 块校验及参数一致性。物理跳帧与稳定性需按[显示检查方法](outputs/手动参数与恢复.md#显示检查)另行测试。
