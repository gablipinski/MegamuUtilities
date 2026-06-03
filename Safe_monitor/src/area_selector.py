import tkinter as tk
from typing import Optional

import mss
from PIL import Image, ImageTk

from config import WindowConfig


class ScreenAreaOverlay:
    def __init__(self, image: Image.Image, offset_x: int, offset_y: int, help_text: Optional[str] = None, parent: Optional[tk.Misc] = None):
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.image = image
        self.photo = None
        self.help_text = help_text or "Drag to select scan area | Enter: confirm | Esc: cancel"

        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("Safe Monitor - Select Scan Area")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)

        self.canvas = tk.Canvas(self.root, cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = 0
        self.start_y = 0
        self.end_x = 0
        self.end_y = 0
        self.rect_id = None
        self.selected = None

        self._draw_background()
        self._draw_help_text()
        self._bind_events()

    def _draw_background(self):
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

    def _draw_help_text(self):
        self.canvas.create_rectangle(10, 10, 740, 45, fill="black", outline="")
        self.canvas.create_text(20, 28, text=self.help_text, fill="white", anchor=tk.W)

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.root.bind("<Return>", self.on_confirm)
        self.root.bind("<Escape>", self.on_cancel)

    def on_mouse_down(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.end_x, self.end_y = event.x, event.y

        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)

        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.end_x,
            self.end_y,
            outline="#ff2d2d",
            width=3,
        )

    def on_mouse_move(self, event):
        self.end_x, self.end_y = event.x, event.y
        if self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, self.end_x, self.end_y)

    def on_mouse_up(self, event):
        self.end_x, self.end_y = event.x, event.y

    def on_confirm(self, _event):
        x1 = min(self.start_x, self.end_x) + self.offset_x
        y1 = min(self.start_y, self.end_y) + self.offset_y
        x2 = max(self.start_x, self.end_x) + self.offset_x
        y2 = max(self.start_y, self.end_y) + self.offset_y

        if abs(x2 - x1) < 20 or abs(y2 - y1) < 20:
            print("[!] Selection too small. Drag a bigger area.")
            return

        self.selected = (x1, y1, x2, y2)
        self.root.destroy()

    def on_cancel(self, _event):
        self.selected = None
        self.root.destroy()

    def select(self):
        if isinstance(self.root, tk.Toplevel):
            self.root.grab_set()
            self.root.wait_window()
        else:
            self.root.mainloop()
        return self.selected


class ScreenPointOverlay:
    def __init__(self, image: Image.Image, offset_x: int, offset_y: int, count: int, help_text: Optional[str] = None, parent: Optional[tk.Misc] = None):
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.image = image
        self.photo = None
        self.count = max(1, count)
        self.help_text = help_text or "Click points in order | Enter: confirm | Esc: cancel"
        self.points: list[tuple[int, int]] = []
        self.selected: list[tuple[int, int]] = []

        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("Safe Monitor - Select Click Points")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)

        self.canvas = tk.Canvas(self.root, cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._draw_background()
        self._draw_help_text()
        self._bind_events()

    def _draw_background(self):
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

    def _draw_help_text(self):
        self.canvas.create_rectangle(10, 10, 840, 45, fill="black", outline="")
        self.canvas.create_text(20, 28, text=self.help_text, fill="white", anchor=tk.W)

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.root.bind("<Return>", self.on_confirm)
        self.root.bind("<Escape>", self.on_cancel)

    def on_mouse_down(self, event):
        if len(self.points) >= self.count:
            return

        self.points.append((event.x, event.y))
        radius = 8
        self.canvas.create_oval(
            event.x - radius,
            event.y - radius,
            event.x + radius,
            event.y + radius,
            fill="#00e676",
            outline="white",
            width=2,
        )
        self.canvas.create_text(
            event.x + 12,
            event.y - 12,
            text=str(len(self.points)),
            fill="white",
            anchor=tk.W,
        )

    def on_confirm(self, _event):
        if len(self.points) != self.count:
            print(f"[!] Selecione exatamente {self.count} ponto(s) antes de confirmar.")
            return

        self.selected = [
            (x + self.offset_x, y + self.offset_y)
            for x, y in self.points
        ]
        self.root.destroy()

    def on_cancel(self, _event):
        self.selected = []
        self.root.destroy()

    def select(self):
        if isinstance(self.root, tk.Toplevel):
            self.root.grab_set()
            self.root.wait_window()
        else:
            self.root.mainloop()
        return self.selected


def capture_virtual_screen() -> tuple[Image.Image, int, int]:
    with mss.MSS() as sct:
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)

    image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    return image, int(monitor["left"]), int(monitor["top"])


def select_area(help_text: Optional[str] = None) -> Optional[tuple[int, int, int, int]]:
    image, offset_x, offset_y = capture_virtual_screen()
    selector = ScreenAreaOverlay(image, offset_x, offset_y, help_text=help_text)
    return selector.select()


def select_area_with_parent(parent: tk.Misc, help_text: Optional[str] = None) -> Optional[tuple[int, int, int, int]]:
    image, offset_x, offset_y = capture_virtual_screen()
    selector = ScreenAreaOverlay(image, offset_x, offset_y, help_text=help_text, parent=parent)
    return selector.select()


def select_points(count: int = 2, help_text: Optional[str] = None) -> list[tuple[int, int]]:
    image, offset_x, offset_y = capture_virtual_screen()
    selector = ScreenPointOverlay(image, offset_x, offset_y, count=count, help_text=help_text)
    return selector.select()


def select_points_with_parent(parent: tk.Misc, count: int = 2, help_text: Optional[str] = None) -> list[tuple[int, int]]:
    image, offset_x, offset_y = capture_virtual_screen()
    selector = ScreenPointOverlay(image, offset_x, offset_y, count=count, help_text=help_text, parent=parent)
    return selector.select()


def build_windows_interactively() -> list[WindowConfig]:
    """Wizard interativo: usuário seleciona região e informa o mapa de cada janela."""
    windows: list[WindowConfig] = []
    index = 1

    while True:
        print(f"\n[🧭] Selecione a REGIAO #{index} na tela.")
        print("    Dica: selecione somente a área onde nomes aparecem.")

        selection = select_area(
            help_text=f"Region #{index}: drag to select | Enter: confirm | Esc: cancel"
        )

        if selection is None:
            if windows:
                start_now = input("[?] Seleção cancelada. Iniciar scan com regiões já adicionadas? (y/n): ").strip().lower()
                if start_now in {"y", "yes", "s", "sim"}:
                    break

            print("[!] Nenhuma nova região adicionada.")
            continue

        x1, y1, x2, y2 = selection
        width = x2 - x1
        height = y2 - y1

        while True:
            map_name = input(f"[?] Nome do mapa para a REGIAO #{index}: ").strip()
            if map_name:
                break
            print("[!] Nome do mapa não pode ser vazio.")

        window = WindowConfig(
            position=f"region-{index}",
            x=int(x1),
            y=int(y1),
            width=int(width),
            height=int(height),
            map_name=map_name,
        )
        windows.append(window)

        print(
            f"[✓] Região #{index} adicionada | mapa='{map_name}' | "
            f"x={window.x}, y={window.y}, w={window.width}, h={window.height}"
        )

        add_more = input("[?] Deseja adicionar outra região? (y/n): ").strip().lower()
        if add_more not in {"y", "yes", "s", "sim"}:
            break

        index += 1

    return windows
