"""
Full-screen alert for a new signal. The dashboard and the scanner start it in its own window process for each
new signal (alerts.show_fullscreen_alert), so it covers the whole screen, on top of every window, until you
close it: Esc or the Close button. "Open Signal desk" opens the dashboard in your browser.

    python fullscreen_alert.py --test     see what it looks like (a clearly marked test, not a signal)

It only shows the signal. Nothing is sent to MT5 from here.
"""
from __future__ import annotations

import json
import sys
import time
import webbrowser

TEST = {
    "side": "BUY", "symbol": "XAUUSD", "strategy": "Gold 5-minute swing", "setup": "pullback",
    "entry": "4059.03", "stop": "4051.20", "target": "4078.61", "half": "4062.95", "size": "0.32 lots",
    "risk": "250.00 USD", "signalled": "", "valid_until": 0, "valid_text": "", "watch": False,
    "desk_url": "http://localhost:8501", "test": True,
}

GREEN, RED, SLATE_BG = "#157A4F", "#B4362E", "#2B3540"
INK, SLATE, AMBER, AMBER_SOFT = "#1C2630", "#5B6B7A", "#8A6100", "#FFF4D6"


def colours(info: dict) -> dict:
    """Green screen for BUY, red for SELL. A watch-only signal gets a dark screen, so it never looks like one
    you can trade; its BUY/SELL word keeps a lighter green or red."""
    buy = info.get("side") == "BUY"
    if info.get("watch"):
        return {"bg": SLATE_BG, "word": "#6FD8A2" if buy else "#FF8F85", "text": "#FFFFFF", "soft": "#C5CED8",
                "button": "#3D4956"}
    return {"bg": GREEN if buy else RED, "word": "#FFFFFF", "text": "#FFFFFF",
            "soft": "#DDF1E6" if buy else "#FBE4E1", "button": "#0F5C3B" if buy else "#8E2A23"}


def beep(root) -> None:
    try:
        import winsound
        winsound.MessageBeep(0x30)  # the Windows exclamation sound
    except Exception:
        root.bell()


class AlertWindow:
    def __init__(self, root, info: dict):
        import tkinter as tk
        import tkinter.font as tkfont

        self.root, self.info, self.tk = root, info, tk
        c = self.c = colours(info)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        k = max(0.55, min(sw / 1920, sh / 1080))

        def font(px, bold=False):
            return tkfont.Font(root=root, family="Segoe UI", size=-int(px * k), weight="bold" if bold else "normal")

        root.title(f"Signal desk: {info.get('side')} {info.get('symbol')}")
        root.configure(bg=c["bg"])
        root.geometry(f"{sw}x{sh}+0+0")  # the whole screen, even where full-screen mode is not available
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.bind("<Escape>", lambda _e: self.close())

        if info.get("test"):
            tk.Label(root, text="Test alert: this is not a real signal. Do not trade it.", bg=AMBER_SOFT, fg=AMBER,
                     font=font(26, True), pady=int(12 * k)).pack(side="top", fill="x")

        box = tk.Frame(root, bg=c["bg"])
        box.place(relx=0.5, rely=0.5, anchor="center")
        when = f" at {info['signalled']} on your PC" if info.get("signalled") else ""
        tk.Label(box, text=f"New signal{when}", bg=c["bg"], fg=c["soft"], font=font(30)).pack()

        word = f"{info.get('side', '')} {info.get('symbol', '')}"
        wf = font(170, True)
        while wf.measure(word) > sw * 0.9 and -wf.cget("size") > 40:  # always fits on one line
            wf.configure(size=int(wf.cget("size") * 0.9))
        tk.Label(box, text=word, bg=c["bg"], fg=c["word"], font=wf).pack(pady=(0, int(4 * k)))

        sub = info.get("strategy", "")
        if sub:
            sub += " rules" + (f", {info['setup']} setup" if info.get("setup") else "")
            tk.Label(box, text=sub, bg=c["bg"], fg=c["text"], font=font(30)).pack()

        if info.get("entry"):
            card = tk.Frame(box, bg="#FFFFFF", padx=int(44 * k), pady=int(26 * k))
            card.pack(pady=(int(30 * k), int(10 * k)))
            cells = [("Entry about", info["entry"], INK), ("Stop-loss", info.get("stop", "-"), RED),
                     ("Target", info.get("target", "-"), GREEN)]
            if info.get("half"):
                cells.append(("Half takes profit at", info["half"], GREEN))
            for col, (label, value, colour) in enumerate(cells):
                tk.Label(card, text=label, bg="#FFFFFF", fg=SLATE, font=font(22)).grid(
                    row=0, column=col, padx=int(26 * k), sticky="w")
                tk.Label(card, text=value, bg="#FFFFFF", fg=colour, font=font(56, True)).grid(
                    row=1, column=col, padx=int(26 * k), sticky="w")
            size = ", ".join(x for x in (f"Size {info['size']}" if info.get("size") else "",
                                         f"risk {info['risk']}" if info.get("risk") else "") if x)
            if size:
                tk.Label(card, text=size, bg="#FFFFFF", fg=INK, font=font(24)).grid(
                    row=2, column=0, columnspan=len(cells), padx=int(26 * k), pady=(int(14 * k), 0), sticky="w")

        self.valid = tk.Label(box, text="", bg=c["bg"], fg=c["text"], font=font(30, True))
        self.valid.pack(pady=(int(14 * k), 0))
        if info.get("watch"):
            tk.Label(box, text="Watch only: this market or strategy lost money in testing. Information only.",
                     bg=AMBER_SOFT, fg=AMBER, font=font(24, True), padx=int(18 * k), pady=int(8 * k)).pack(
                pady=(int(14 * k), 0))
        tk.Label(box, text="Signal only: you decide whether to trade. Nothing is sent to MT5 until you click "
                           "Place order and then Confirm on the desk.",
                 bg=c["bg"], fg=c["soft"], font=font(22), wraplength=int(sw * 0.8)).pack(pady=(int(14 * k), 0))

        row = tk.Frame(box, bg=c["bg"])
        row.pack(pady=(int(30 * k), 0))
        tk.Button(row, text="Open Signal desk", command=self.open_desk, bg="#FFFFFF", fg=c["bg"],
                  activebackground="#F1F3F5", activeforeground=c["bg"], relief="flat", cursor="hand2",
                  font=font(30, True), padx=int(34 * k), pady=int(12 * k)).pack(side="left", padx=int(12 * k))
        tk.Button(row, text="Close   (Esc)", command=self.close, bg=c["button"], fg="#FFFFFF",
                  activebackground=c["button"], activeforeground="#FFFFFF", relief="flat", cursor="hand2",
                  font=font(30, True), padx=int(34 * k), pady=int(12 * k)).pack(side="left", padx=int(12 * k))

        self.tick()
        root.after(150, self.to_front)
        root.after(100, lambda: beep(root))
        root.after(1600, lambda: beep(root))

    def tick(self) -> None:
        until = float(self.info.get("valid_until") or 0)
        left = until - time.time()
        if not until:
            self.valid.configure(text="")
        elif left > 0:
            m, s = divmod(int(left), 60)
            self.valid.configure(text=f"Valid for {m}:{s:02d} more, until {self.info.get('valid_text', '')}")
            self.root.after(1000, self.tick)
        else:
            self.valid.configure(text=f"This signal ended at {self.info.get('valid_text', '')}: its candle has "
                                      "closed, so its plan no longer applies.", fg=self.c["soft"])

    def to_front(self) -> None:
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()

    def open_desk(self) -> None:
        try:
            webbrowser.open(self.info.get("desk_url") or "http://localhost:8501")
        finally:
            self.close()

    def close(self) -> None:
        self.root.destroy()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    info = dict(TEST) if not argv or argv[0] == "--test" else json.loads(argv[0])
    if sys.platform == "win32":
        try:  # sharp text on high-resolution screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk

    root = tk.Tk()
    AlertWindow(root, info)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
