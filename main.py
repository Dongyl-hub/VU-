"""Windows 双声道实时 VU 电平表。"""

from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import numpy as np
    import soundcard as sc
except ImportError:
    np = None
    sc = None


BACKGROUND = "#111720"
PANEL = "#1a2432"
PANEL_BORDER = "#2d3b4d"
TEXT = "#e7edf5"
MUTED_TEXT = "#9aa9ba"
GREEN_OFF = "#0e2809"
YELLOW_OFF = "#403a1d"
RED_OFF = "#2e0008"
GREEN_ON = "#00ff00"
YELLOW_ON = "#ffff00"
RED_ON = "#ff0000"


def clamp(value: float, minimum: float, maximum: float) -> float:
    """作用：将数值限制在指定范围内。输入：待限制值、最小值和最大值。输出：限制后的浮点数。"""
    return max(minimum, min(maximum, value))


def amplitude_to_db(amplitude: float) -> float:
    """作用：把线性音频幅度转换为 dBFS。输入：非负线性幅度。输出：-48.0 至 0.0 的分贝值。"""
    if amplitude <= 1e-12:
        return -48.0
    return clamp(20.0 * math.log10(amplitude), -48.0, 0.0)


class AudioCapture:
    """使用 WASAPI 回环持续采集默认播放设备的双声道音频。"""

    def __init__(self, output_queue: queue.Queue[tuple[float, float]], device_name: str = "") -> None:
        """作用：初始化音频采集器。输入：电平数据队列和要监测的回环设备名称。输出：无。"""
        self.output_queue = output_queue
        self.device_name = device_name
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> None:
        """作用：在后台线程启动系统声音采集。输入：无。输出：无。"""
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """作用：停止后台采集线程并等待其退出。输入：无。输出：无。"""
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)

    def _find_loopback_device(self):
        """作用：查找用户选择或默认扬声器对应的回环设备。输入：无。输出：SoundCard 回环麦克风对象。"""
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("没有找到默认播放设备")
        devices = sc.all_microphones(include_loopback=True)
        if self.device_name:
            for device in devices:
                if getattr(device, "isloopback", False) and device.name == self.device_name:
                    return device
            raise RuntimeError(f"播放设备不可用：{self.device_name}")
        for device in devices:
            if getattr(device, "isloopback", False) and speaker.name in device.name:
                return device
        for device in devices:
            if getattr(device, "isloopback", False):
                return device
        raise RuntimeError("没有找到 WASAPI 回环设备")

    def _publish_level(self, left: float, right: float) -> None:
        """作用：将最新双声道幅度放入队列并丢弃过期数据。输入：左右声道线性幅度。输出：无。"""
        try:
            self.output_queue.put_nowait((left, right))
        except queue.Full:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                pass
            self.output_queue.put_nowait((left, right))

    def _capture_loop(self) -> None:
        """作用：循环录制系统回环音频并计算各声道 RMS 电平。输入：无。输出：无，通过队列发送结果。"""
        try:
            loopback = self._find_loopback_device()
            with loopback.recorder(samplerate=48000, channels=2, blocksize=1024) as recorder:
                while not self.stop_event.is_set():
                    data = recorder.record(numframes=1024)
                    if data.size == 0:
                        continue
                    if data.ndim == 1 or data.shape[1] == 1:
                        rms = float(np.sqrt(np.mean(np.square(data))))
                        self._publish_level(rms, rms)
                    else:
                        rms = np.sqrt(np.mean(np.square(data[:, :2]), axis=0))
                        self._publish_level(float(rms[0]), float(rms[1]))
        except Exception as exc:
            self.error = str(exc)


class VuMeter(tk.Canvas):
    """绘制与参考图相近的双声道分段式 VU 表。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        """作用：创建并初始化 VU 表画布。输入：父控件和 Canvas 参数。输出：无。"""
        super().__init__(master, width=300, height=430, bg=PANEL, highlightthickness=1,
                         highlightbackground=PANEL_BORDER, **kwargs)
        self.segment_count = 28
        self.segment_items: list[list[int]] = [[], []]
        self.value_items: list[int] = []
        self._draw_meter()

    def _segment_color(self, index: int, active: bool) -> str:
        """作用：根据灯段位置和状态返回颜色。输入：从底部起的灯段序号及是否点亮。输出：颜色字符串。"""
        ratio = index / (self.segment_count - 1)
        if ratio >= 0.90:
            return RED_ON if active else RED_OFF
        if ratio >= 0.66:
            return YELLOW_ON if active else YELLOW_OFF
        return GREEN_ON if active else GREEN_OFF

    def _draw_meter(self) -> None:
        """作用：绘制表盘刻度、标签和左右声道灯段。输入：无。输出：无。"""
        meter_top, meter_bottom = 42, 378
        bar_width, segment_gap = 46, 3
        segment_height = (meter_bottom - meter_top - segment_gap * (self.segment_count - 1)) / self.segment_count
        centers = (103, 211)

        for channel, center_x in enumerate(centers):
            value_id = self.create_text(center_x, 20, text="-48.0 dB", fill=TEXT,
                                        font=("Consolas", 11), tags="value")
            self.value_items.append(value_id)
            self.create_rectangle(center_x - 43, 8, center_x + 43, 33, fill=BACKGROUND, outline="")
            self.tag_raise(value_id)

            for index in range(self.segment_count):
                y2 = meter_bottom - index * (segment_height + segment_gap)
                y1 = y2 - segment_height
                item = self.create_rectangle(center_x - bar_width / 2, y1,
                                             center_x + bar_width / 2, y2,
                                             fill=self._segment_color(index, False),
                                             outline="#0d1219", width=1)
                self.segment_items[channel].append(item)

            self.create_rectangle(center_x - 43, 389, center_x + 43, 411, fill=BACKGROUND, outline="")
            self.create_text(center_x, 400, text="L" if channel == 0 else "R",
                             fill=TEXT, font=("Consolas", 11))

        for db in (0, -6, -12, -24, -36, -48):
            y = meter_top + (-db / 48.0) * (meter_bottom - meter_top)
            self.create_text(50, y, text=f"{db:g}", fill=MUTED_TEXT, font=("Consolas", 9))
            self.create_line(57, y, 64, y, fill=MUTED_TEXT)
            self.create_text(158, y, text=f"{db:g}", fill=MUTED_TEXT, font=("Consolas", 9))
            self.create_line(165, y, 172, y, fill=MUTED_TEXT)

    def set_levels(self, left_db: float, right_db: float) -> None:
        """作用：更新左右声道数值和点亮灯段。输入：左右声道 dBFS 值。输出：无。"""
        for channel, db in enumerate((left_db, right_db)):
            db = clamp(db, -48.0, 0.0)
            self.itemconfigure(self.value_items[channel], text=f"{db:5.1f} dB")
            active_count = int(round((db + 48.0) / 48.0 * self.segment_count))
            for index, item in enumerate(self.segment_items[channel]):
                self.itemconfigure(item, fill=self._segment_color(index, index < active_count))
                

class VuMeterApp:
    """管理窗口、音频数据和电平回落动画。"""

    def __init__(self, root: tk.Tk) -> None:
        """作用：初始化应用窗口及运行状态。输入：Tk 根窗口。输出：无。"""
        self.root = root
        self.level_queue: queue.Queue[tuple[float, float]] = queue.Queue(maxsize=2)
        self.device_names, default_device = self._get_output_devices()
        self.selected_device = tk.StringVar(value=default_device)
        self.capture = AudioCapture(self.level_queue, default_device)
        self.current_db = [-48.0, -48.0]
        self.target_db = [-48.0, -48.0]
        self.last_error: str | None = None
        self.sensitivity = tk.DoubleVar(value=-2.0)
        self.fall_speed = tk.DoubleVar(value=50.0)
        self.status = tk.StringVar(value="正在连接播放设备…")
        self._configure_window()
        self._build_ui()
        self.capture.start()
        self.root.after(20, self._update)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        """作用：设置主窗口标题、尺寸和主题。输入：无。输出：无。"""
        self.root.title("实时双声道 VU 电平表")
        self.root.geometry("430x635")
        self.root.minsize(390, 605)
        self.root.configure(bg=BACKGROUND)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TScale", background=BACKGROUND, troughcolor=PANEL)
        style.configure("Device.TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=PANEL_BORDER)
        style.map("Device.TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL)])

    def _get_output_devices(self) -> tuple[list[str], str]:
        """作用：获取所有可监测的播放回环设备及默认项。输入：无。输出：设备名称列表和默认设备名称。"""
        loopbacks = [device.name for device in sc.all_microphones(include_loopback=True)
                     if getattr(device, "isloopback", False)]
        loopbacks = list(dict.fromkeys(loopbacks))
        if not loopbacks:
            return [], ""
        speaker = sc.default_speaker()
        if speaker is not None:
            for name in loopbacks:
                if speaker.name in name:
                    return loopbacks, name
        return loopbacks, loopbacks[0]

    def _build_ui(self) -> None:
        """作用：创建表盘、调节控件和状态文字。输入：无。输出：无。"""
        title = tk.Label(self.root, text="SYSTEM AUDIO  ·  VU METER", bg=BACKGROUND,
                         fg=TEXT, font=("Segoe UI Semibold", 13))
        title.pack(pady=(15, 8))
        self.meter = VuMeter(self.root)
        self.meter.pack(padx=16)

        controls = tk.Frame(self.root, bg=BACKGROUND)
        controls.pack(fill="x", padx=24, pady=(10, 0))
        self._add_slider(controls, 0, "灵敏度", self.sensitivity, -24.0, 24.0, "dB")
        self._add_slider(controls, 1, "回落速度", self.fall_speed, 6.0, 60.0, "dB/s")

        device_frame = tk.Frame(self.root, bg=BACKGROUND)
        device_frame.pack(fill="x", padx=24, pady=(9, 0))
        tk.Label(device_frame, text="播放设备", bg=BACKGROUND, fg=TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.device_box = ttk.Combobox(device_frame, textvariable=self.selected_device,
                                       values=self.device_names, state="readonly",
                                       style="Device.TCombobox", font=("Microsoft YaHei UI", 9))
        self.device_box.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.device_box.bind("<<ComboboxSelected>>", self._on_device_changed)
        tk.Label(self.root, textvariable=self.status, bg=BACKGROUND, fg=MUTED_TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(pady=(8, 0))

    def _on_device_changed(self, _event=None) -> None:
        """作用：响应下拉栏选择并切换实时音频采集设备。输入：可选的 Tk 事件对象。输出：无。"""
        device_name = self.selected_device.get()
        if not device_name:
            self.status.set("没有可用的播放回环设备")
            return
        self.status.set(f"正在切换到：{device_name}")
        self.capture.stop()
        while not self.level_queue.empty():
            try:
                self.level_queue.get_nowait()
            except queue.Empty:
                break
        self.current_db = [-48.0, -48.0]
        self.target_db = [-48.0, -48.0]
        self.last_error = None
        self.capture = AudioCapture(self.level_queue, device_name)
        self.capture.start()

    def _add_slider(self, parent: tk.Misc, row: int, label: str, variable: tk.DoubleVar,
                    minimum: float, maximum: float, unit: str) -> None:
        """作用：添加一个带实时数值的调节滑块。输入：父控件、行号、名称、变量、范围和单位。输出：无。"""
        tk.Label(parent, text=label, bg=BACKGROUND, fg=TEXT,
                 font=("Microsoft YaHei UI", 9)).grid(row=row, column=0, sticky="w")
        scale = ttk.Scale(parent, variable=variable, from_=minimum, to=maximum)
        scale.grid(row=row, column=1, sticky="ew", padx=10)
        value_label = tk.Label(parent, bg=BACKGROUND, fg=TEXT, width=9,
                               font=("Consolas", 9))
        value_label.grid(row=row, column=2, sticky="e")

        def refresh_label(*_args) -> None:
            """作用：刷新滑块右侧的数值文字。输入：Tk 变量回调参数。输出：无。"""
            value_label.configure(text=f"{variable.get():.1f} {unit}")

        variable.trace_add("write", refresh_label)
        refresh_label()
        parent.columnconfigure(1, weight=1)

    def _consume_audio(self) -> None:
        """作用：读取最新音频幅度并换算成带灵敏度补偿的目标电平。输入：无。输出：无。"""
        latest = None
        while True:
            try:
                latest = self.level_queue.get_nowait()    
            except queue.Empty:
                break
        if latest is not None:
            gain = 10.0 ** (self.sensitivity.get() / 20.0)
            self.target_db[0] = amplitude_to_db(latest[0] * gain)
            self.target_db[1] = amplitude_to_db(latest[1] * gain)
            self.status.set(f"正在监测：{self.selected_device.get()}")

    def _update(self) -> None:
        """作用：按固定帧率更新音频数据、回落动画和界面。输入：无。输出：无。"""
        self._consume_audio()
        fall_per_frame = self.fall_speed.get() * 0.020
        for channel in range(2):
            if self.target_db[channel] >= self.current_db[channel]:
                self.current_db[channel] = self.target_db[channel]
            else:
                self.current_db[channel] = max(self.target_db[channel],
                                               self.current_db[channel] - fall_per_frame)
        self.meter.set_levels(*self.current_db)
        if self.capture.error and self.capture.error != self.last_error:
            self.last_error = self.capture.error
            self.status.set(f"采集失败：{self.capture.error}")
        self.root.after(20, self._update)

    def _on_close(self) -> None:
        """作用：安全停止采集并关闭程序。输入：无。输出：无。"""
        self.capture.stop()
        self.root.destroy()


def check_dependencies() -> bool:
    """作用：检查实时音频所需第三方库。输入：无。输出：依赖齐全时返回 True，否则返回 False。"""
    if np is not None and sc is not None:
        return True
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("缺少依赖", "请先运行：\n\npip install -r requirements.txt")
    root.destroy()
    return False


def main() -> None:
    """作用：检查依赖并启动 VU 电平表主程序。输入：无。输出：无。"""
    if not check_dependencies():
        return
    root = tk.Tk()
    VuMeterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
