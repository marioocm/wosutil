"""Self-updating tooltip widget for the WoS Util GUI."""

import tkinter as tk


class Tooltip:
    """Shows a hover tooltip whose text is fetched and refreshed dynamically.

    The text is obtained via ``get_text`` each time the tooltip is displayed
    and then refreshed every second while visible, so live countdowns stay up
    to date. The tooltip appears under the widget after a short hover delay
    and is hidden on leave or click.
    """

    SHOW_DELAY_MS = 500
    REFRESH_MS = 1000

    def __init__(self, widget, get_text):
        """Bind hover and click events on the widget to show the tooltip.

        Args:
            widget: The widget the tooltip belongs to.
            get_text: Callable returning the tooltip text; called every time
                the tooltip is shown and while it stays visible.
        """
        self._widget = widget
        self._get_text = get_text
        self._window = None
        self._label = None
        self._show_after = None
        self._pinned = False
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<Button-1>", self._on_click)
        # A click anywhere else in the window closes the pinned tooltip.
        self._root = widget.winfo_toplevel()
        self._root.bind("<Button-1>", self._on_root_click, add="+")

    def show(self):
        """Show the tooltip immediately and keep it visible until hidden."""
        self._pinned = True
        self._cancel_show()
        self._show()

    def hide(self):
        """Hide the tooltip and stop keeping it visible."""
        self._pinned = False
        self._cancel_show()
        self._hide()

    def _on_enter(self, _event):
        """Start the hover delay before showing the tooltip."""
        if self._pinned or self._window is not None:
            return
        self._show_after = self._widget.after(self.SHOW_DELAY_MS, self._show)

    def _on_leave(self, _event):
        """Cancel a pending show and hide the tooltip unless it is pinned."""
        if self._pinned:
            return
        self._cancel_show()
        self._hide()

    def _on_click(self, _event):
        """Toggle the tooltip: click shows it, clicking again hides it."""
        if self._window is not None:
            self.hide()
        else:
            self.show()

    def _on_root_click(self, event):
        """Close a pinned tooltip when clicking anywhere else in the window.

        Clicks on the tooltip's own widget are handled by _on_click; clicks
        on the tooltip window itself do not reach this binding (the tooltip
        window is a separate Toplevel).
        """
        try:
            if event.widget is self._widget or not self._pinned:
                return
            self.hide()
        except tk.TclError:
            # The widget was destroyed between bindings (rows are rebuilt on
            # refresh); nothing left to hide.
            pass

    def _cancel_show(self) -> None:
        """Cancel the delayed show if it is still pending."""
        if self._show_after is not None:
            self._widget.after_cancel(self._show_after)
            self._show_after = None

    def _show(self) -> None:
        """Create the tooltip window, position it and keep it refreshed."""
        self._show_after = None
        text = self._get_text()
        if not text:
            return
        window = tk.Toplevel(self._widget)
        window.wm_overrideredirect(True)
        window.wm_attributes("-topmost", True)
        label = tk.Label(
            window,
            text=text,
            justify="left",
            bg="#FFFFE1",
            fg="#333333",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
            font=("Segoe UI", 9),
        )
        label.pack()
        self._window = window
        self._label = label
        self._position(window)
        self._refresh()

    def _position(self, window: tk.Toplevel) -> None:
        """Center the tooltip under the widget, flipping above if needed."""
        window.update_idletasks()
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2 - window.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 8
        if y + window.winfo_height() > window.winfo_screenheight():
            y = self._widget.winfo_rooty() - window.winfo_height() - 8
        window.wm_geometry(f"+{x}+{y}")

    def _refresh(self) -> None:
        """Update the tooltip text while it stays visible."""
        if self._window is None:
            return
        if self._label is not None:
            self._label.config(text=self._get_text())
        self._position(self._window)
        self._window.after(self.REFRESH_MS, self._refresh)

    def _hide(self) -> None:
        """Destroy the tooltip window if it is showing."""
        if self._window is not None:
            self._window.destroy()
            self._window = None
            self._label = None
