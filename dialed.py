"""
dialed.gg Color Swatch Overlay
================================
Reads the memorized color from #memorize-color in the browser
and shows a floating color swatch you can drag next to the picker.

Requirements:
    pip install selenium webdriver-manager

How to run:
    1. Run this script (python dialed.py) — it opens Chrome and goes to dialed.gg
    2. Start the game — swatch auto-updates each memorize phase
    3. Click AUTO SET during picker phase to set sliders automatically
    4. Right-click overlay to close
"""

import tkinter as tk
import threading
import time
import re
import colorsys

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager


POLL_MS = 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_rgb(style):
    m = re.search(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', style or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None

def rgb_to_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"

def perceived_lightness(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


# ── Browser ───────────────────────────────────────────────────────────────────

def start_browser():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts
    )
    driver.get("https://dialed.gg/")
    return driver

def get_memorize_color(driver):
    try:
        el = driver.find_element(By.ID, "memorize-color")
        return parse_rgb(el.get_attribute("style"))
    except Exception:
        return None


# ── Read game's own HSB from #picker-values ───────────────────────────────────
# The game shows e.g. "H130 S90 B76" — this is ground truth

def read_game_hsb(driver):
    try:
        text = driver.find_element(By.ID, "picker-values").text
        m = re.search(r'H(\d+)\s+S(\d+)\s+B(\d+)', text)
        if m:
            return int(m[1]), int(m[2]), int(m[3])
    except Exception:
        pass
    return None


# ── Convert target RGB to the game's HSB space ───────────────────────────────
# The game uses standard HSV but we verify by cross-checking picker-values

def rgb_to_hsb(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 360), round(s * 100), round(v * 100)


# ── Set handles using JS (pixel-perfect, no drag error) ───────────────────────
#
# Strip orientation confirmed from DOM:
#   h-strip: top 0% = H0 (red), top 100% = H360 (red)  → top% = H/360
#   s-strip: gradient from full-color (top) to gray (bottom)
#            top 0% = S100, top 100% = S0               → top% = (100-S)/100
#   b-strip: gradient from bright (top) to black (bottom)
#            top 0% = B100, top 100% = B0               → top% = (100-B)/100

def auto_set_handles(driver, rgb):
    target_h, target_s, target_b = rgb_to_hsb(*rgb)
    print(f"  Target HSB: H={target_h} S={target_s} B={target_b}")

    h_pct = target_h / 360
    s_pct = (100 - target_s) / 100
    b_pct = (100 - target_b) / 100

    # Clamp to valid range
    h_pct = max(0.005, min(0.995, h_pct))
    s_pct = max(0.005, min(0.995, s_pct))
    b_pct = max(0.005, min(0.995, b_pct))

    print(f"  Handle positions: h={h_pct:.3f} s={s_pct:.3f} b={b_pct:.3f}")

    # Use JS to set top% directly — bypasses drag inaccuracy
    # Simulate real mouse drag at exact pixel coords — game reads clientY not style.top
    driver.execute_script("""
        function setHandle(handleId, stripId, pct) {
            const handle = document.getElementById(handleId);
            const strip  = document.getElementById(stripId);
            if (!handle || !strip) return;

            const rect = strip.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + pct * rect.height;

            function fire(el, type) {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true,
                    view: window, clientX: x, clientY: y
                }));
            }

            // simulate full drag sequence exactly like a real user
            fire(strip,    'mousedown');
            fire(document, 'mousemove');
            fire(strip,    'mousemove');
            fire(document, 'mouseup');
            fire(strip,    'mouseup');

            // also set style in case game uses it as fallback
            handle.style.top = (pct * 100).toFixed(4) + '%';
        }

        setHandle('h-handle', 'h-strip', arguments[0]);
        setHandle('s-handle', 's-strip', arguments[1]);
        setHandle('b-handle', 'b-strip', arguments[2]);
    """, h_pct, s_pct, b_pct)

    time.sleep(0.3)

    # Read back what the game shows and print for debugging
    actual = read_game_hsb(driver)
    if actual:
        print(f"  Game reports: H={actual[0]} S={actual[1]} B={actual[2]}")
        print(f"  Delta:        H={abs(target_h-actual[0])} S={abs(target_s-actual[1])} B={abs(target_b-actual[2])}")
    return actual


# ── Overlay UI ────────────────────────────────────────────────────────────────

class SwatchOverlay:
    def __init__(self, driver):
        self.driver = driver
        self.current_rgb = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.geometry("160x215+100+100")
        self.root.configure(bg="#111")

        # ── Big color swatch ──
        self.swatch = tk.Canvas(
            self.root, width=160, height=110,
            bg="#222", highlightthickness=0
        )
        self.swatch.pack()

        # ── RGB label ──
        self.rgb_var = tk.StringVar(value="waiting...")
        tk.Label(self.root, textvariable=self.rgb_var,
                 bg="#111", fg="#888", font=("Courier", 9), pady=4).pack()

        # ── Round label ──
        self.round_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.round_var,
                 bg="#111", fg="#555", font=("Courier", 8)).pack()

        # ── AUTO SET button ──
        self.auto_btn = tk.Label(
            self.root, text="AUTO SET",
            bg="#1a1a1a", fg="#4ade80",
            font=("Courier", 10, "bold"),
            pady=6, cursor="hand2", width=14
        )
        self.auto_btn.pack(pady=(8, 0))
        self.auto_btn.bind("<Button-1>", self._on_auto_set)
        self.auto_btn.bind("<Enter>", lambda e: self.auto_btn.config(bg="#222"))
        self.auto_btn.bind("<Leave>", lambda e: self.auto_btn.config(bg="#1a1a1a"))

        self.status_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status_var,
                 bg="#111", fg="#555", font=("Courier", 8)).pack(pady=2)

        # Drag
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>",     self._drag_move)
        # Close on right-click
        self.root.bind("<Button-3>", lambda e: self.root.destroy())

        self._poll()
        self.root.mainloop()

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _poll(self):
        try:
            rgb = get_memorize_color(self.driver)
            if rgb and rgb != (0, 0, 0) and rgb != self.current_rgb:
                self.current_rgb = rgb
                r, g, b = rgb
                self.swatch.config(bg=rgb_to_hex(r, g, b))
                self.swatch.delete("all")
                self.rgb_var.set(f"rgb({r}, {g}, {b})")
                self.status_var.set("")
                try:
                    rnd = self.driver.find_element(By.ID, "memorize-round").text
                    self.round_var.set(f"round {rnd}")
                except Exception:
                    pass
        except Exception:
            pass
        self.root.after(POLL_MS, self._poll)

    def _on_auto_set(self, event=None):
        if not self.current_rgb:
            self.status_var.set("no color yet!")
            return
        self.status_var.set("setting...")
        self.auto_btn.config(fg="#888")

        def run():
            try:
                actual = auto_set_handles(self.driver, self.current_rgb)
                if actual:
                    msg = f"H{actual[0]} S{actual[1]} B{actual[2]}"
                else:
                    msg = "done ✓"
                self.root.after(0, lambda: self.status_var.set(msg))
            except Exception as ex:
                self.root.after(0, lambda: self.status_var.set(f"err: {str(ex)[:18]}"))
            finally:
                self.root.after(0, lambda: self.auto_btn.config(fg="#4ade80"))

        threading.Thread(target=run, daemon=True).start()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting browser...")
    driver = start_browser()
    print("Browser ready — start the game!")
    print("Right-click overlay to close.\n")

    try:
        SwatchOverlay(driver)
    finally:
        driver.quit()