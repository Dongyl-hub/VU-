# 实时双声道 VU 电平表

这是一个 Windows 桌面 VU 电平表，通过 WASAPI 回环读取默认播放设备正在播放的声音，不会使用麦克风。

## 安装和运行

需要 Python 3.10 或更高版本。在本目录打开 PowerShell：

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 控件

- **灵敏度**：对采集到的信号施加 `-24 dB` 至 `+24 dB` 增益。
- **回落速度**：设置灯条从峰值下降的速度，范围为 `6–60 dB/s`。

程序监测启动时的 Windows 默认播放设备。如果运行期间切换了默认设备，请重启程序。
