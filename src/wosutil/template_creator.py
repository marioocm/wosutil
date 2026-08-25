"""Template Creator - Standalone application for creating image templates.

This application captures emulator screenshots and creates templates for image recognition.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import pyperclip
from PIL import Image, ImageTk

from wosutil.config import TEMPLATES_DIR
from wosutil.emulator.emulator_manager import take_screenshot
from wosutil.emulator.instances_controller import MultiInstanceManager


class TemplateCreatorApp:
    """Standalone application for creating image templates for template matching."""

    def __init__(self):
        """Initialize the template creator application."""
        self.root = tk.Tk()
        self.root.title("Template Creator - WoS Util")

        # Configure the window
        window_width = 800
        window_height = 650

        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Compute position to center the window
        x_position = (screen_width - window_width) // 2
        y_position = (screen_height - window_height) // 2

        # Set window geometry
        self.root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.root.configure(bg="#2C3E50")

        # Application variables
        self.image = None
        self.display_image = None  # Scaled image for display
        self.tk_image = None
        self.rect = None
        self.start_x = self.start_y = self.end_x = self.end_y = 0
        self.last_roi = None
        self.zoom = 1.0
        self.selected_instance = tk.StringVar()  # StringVar to handle "Instance X"
        self.multi_instance_manager = MultiInstanceManager(print)  # To get the available instances

        # Variables for panning
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        self.is_panning = False

        self.setup_gui()

    def setup_gui(self):
        """Set up the graphical interface."""
        # Configure the style
        style = ttk.Style()
        style.theme_use("clam")

        # Configure styles
        style.configure("TFrame", background="#34495E")
        style.configure("TLabel", background="#34495E", foreground="#ECF0F1", font=("Arial", 10))
        style.configure("TButton", background="#1ABC9C", foreground="white", font=("Arial", 10, "bold"), borderwidth=0)
        style.map("TButton", background=[("active", "#16A085")])
        style.configure("TCombobox", background="#34495E", foreground="#ECF0F1")

        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Emulator configuration frame
        emulator_frame = ttk.LabelFrame(main_frame, text="Emulator Configuration", padding=10)
        emulator_frame.pack(fill="x", pady=(0, 10))

        # Instance selector
        instance_frame = ttk.Frame(emulator_frame)
        instance_frame.pack(fill="x")

        ttk.Label(instance_frame, text="Emulator instance:").pack(side="left", padx=(0, 10))

        # Combobox for instance selection
        self.instance_combo = ttk.Combobox(instance_frame, textvariable=self.selected_instance, values=[], state="readonly", width=10)
        self.instance_combo.pack(side="left", padx=(0, 10))

        # Button to refresh instances
        ttk.Button(instance_frame, text="Refresh", command=self.refresh_instances).pack(side="left")

        # Label showing the instance status
        self.instance_status_label = ttk.Label(instance_frame, text="Status: Unknown", foreground="orange")
        self.instance_status_label.pack(side="left", padx=(20, 0))

        # Buttons frame
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=5)

        # Buttons
        ttk.Button(btn_frame, text="Load Image", command=self.load_image).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Capture from Emulator", command=self.capture_from_emulator).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Save Template", command=self.save_template).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Save Full Screenshot", command=self.save_full_screenshot).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Zoom +", command=self.zoom_in).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Zoom -", command=self.zoom_out).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Reset View", command=self.reset_view).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Copy ROI", command=self.copy_roi_coordinates).pack(side="left", padx=5)

        # Label showing the ROI
        self.roi_label = ttk.Label(btn_frame, text="ROI: (x, y, w, h)")
        self.roi_label.pack(side="left", padx=10)

        # Canvas for the image
        self.canvas = tk.Canvas(main_frame, cursor="cross", bg="black")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)

        # Canvas bindings
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<ButtonPress-3>", self.on_press)  # Right button
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<B3-Motion>", self.on_drag)  # Drag with right button
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<ButtonRelease-3>", self.on_release)  # Release right button
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)  # Mouse wheel for zoom
        self.canvas.bind("<Configure>", self.on_canvas_configure)  # Resize canvas

        # Keyboard shortcuts
        self.root.bind("<Control-o>", lambda e: self.load_image())
        self.root.bind("<Control-s>", lambda e: self.save_template())
        self.root.bind("<Control-plus>", lambda e: self.zoom_in())
        self.root.bind("<Control-minus>", lambda e: self.zoom_out())
        self.root.bind("<Control-0>", lambda e: self.reset_view())  # Reset view
        self.root.bind("<Escape>", lambda e: self.cancel_selection())

        # Pan shortcuts
        self.root.bind("<Left>", lambda e: self.pan_left())
        self.root.bind("<Right>", lambda e: self.pan_right())
        self.root.bind("<Up>", lambda e: self.pan_up())
        self.root.bind("<Down>", lambda e: self.pan_down())

        # Initialize available instances
        self.refresh_instances()

    def get_instance_number(self):
        """Extract the instance number from the selected string."""
        try:
            selected = self.selected_instance.get()
            if selected and selected.startswith("Instance "):
                return int(selected.split(" ")[1])
            return None
        except (ValueError, IndexError):
            return None

    def refresh_instances(self):
        """Refresh the list of available instances."""
        try:
            # Get available instances
            instances_data = self.multi_instance_manager.get_instances()
            available_instances = [instance["index"] for instance in instances_data]

            if available_instances:
                # Update combobox
                instance_values = [f"Instance {i}" for i in available_instances]
                self.instance_combo["values"] = instance_values

                # Select the first available instance if there is no selection
                current_instance_num = self.get_instance_number()
                if current_instance_num not in available_instances:
                    self.selected_instance.set(f"Instance {available_instances[0]}")
                    self.instance_combo.set(f"Instance {available_instances[0]}")

                # Update status
                current_instance = self.get_instance_number()
                if current_instance in available_instances:
                    self.instance_status_label.config(text=f"Status: Connected (Instance {current_instance})", foreground="light green")
                else:
                    self.instance_status_label.config(text="Status: Not Available", foreground="red")

                print(f"Available instances: {available_instances}")
            else:
                self.instance_combo["values"] = ["No instances available"]
                self.instance_status_label.config(text="Status: No instances available", foreground="red")
                print("No available emulator instances found")

        except Exception as e:
            print(f"Error refreshing instances: {e}")
            self.instance_combo["values"] = ["Error fetching instances"]
            self.instance_status_label.config(text="Status: Error", foreground="red")

    def load_image(self):
        """Load an image from a file."""
        file_path = filedialog.askopenfilename(title="Select image", filetypes=[("PNG Images", "*.png"), ("JPEG Images", "*.jpg"), ("All files", "*.*")])
        if file_path:
            self._load_image_from_path(file_path)

    def capture_from_emulator(self):
        """Capture a screenshot from the selected emulator."""
        try:
            # Get the selected instance
            instance_index = self.get_instance_number()

            if instance_index is None:
                messagebox.showerror("Error", "Please select a valid emulator instance.")
                return

            # Check that the instance is available
            instances_data = self.multi_instance_manager.get_instances()
            available_instances = [instance["index"] for instance in instances_data]
            if instance_index not in available_instances:
                messagebox.showerror("Error", f"Instance {instance_index} is not available.\nAvailable instances: {available_instances}")
                return

            print(f"Capturing emulator screen (instance {instance_index})...")

            screenshot_path = take_screenshot(instance_index)

            if screenshot_path and os.path.exists(screenshot_path):
                self._load_image_from_path(screenshot_path)
                print(f"Temporary capture loaded: {screenshot_path}")
                try:
                    os.remove(screenshot_path)
                    print(f"Temporary capture removed: {screenshot_path}")
                except Exception as e:
                    print(f"Could not remove the temporary capture: {e}")
            else:
                messagebox.showerror(
                    "Error",
                    f"Could not capture the emulator screen (instance {instance_index}).\nMake sure the emulator is running.",
                )
        except Exception as e:
            messagebox.showerror("Error", f"Error capturing screen: {str(e)}")
            print(f"Detailed error: {e}")

    def _load_image_from_path(self, file_path):
        """Load an image from a file path."""
        try:
            self.image = cv2.cvtColor(cv2.imread(file_path), cv2.COLOR_BGR2RGB)
            self.zoom = 1.0
            self.pan_offset_x = 0
            self.pan_offset_y = 0
            self._update_display_image()
            self._draw_image()
            self.rect = None
            self.roi_label.config(text="ROI: (x, y, w, h)")
            self.last_roi = None
            print(f"Image loaded: {file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Error loading the image: {str(e)}")

    def _update_display_image(self):
        """Update the display image with zoom."""
        if self.image is not None:
            h, w, _ = self.image.shape
            new_size = (int(w * self.zoom), int(h * self.zoom))
            pil_img = Image.fromarray(self.image).resize(new_size, Image.Resampling.LANCZOS)
            self.display_image = pil_img
            self.tk_image = ImageTk.PhotoImage(pil_img)

    def _draw_image(self):
        """Draw the image on the canvas with panning."""
        if self.tk_image:
            self.canvas.delete("all")

            # Get canvas and image dimensions
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            img_width = self.tk_image.width()
            img_height = self.tk_image.height()

            # Compute the image position with the pan offset
            x = self.pan_offset_x
            y = self.pan_offset_y

            # Center the image if it is smaller than the canvas
            if img_width < canvas_width:
                x = (canvas_width - img_width) // 2 + self.pan_offset_x
            if img_height < canvas_height:
                y = (canvas_height - img_height) // 2 + self.pan_offset_y

            # Create the image on the canvas
            self.canvas.create_image(x, y, anchor="nw", image=self.tk_image)
            self.rect = None

    def on_mousewheel(self, event):
        """Zoom with the mouse wheel."""
        if self.image is not None:
            if event.delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()

    def on_canvas_configure(self, event):
        """Handle canvas resize."""
        if self.tk_image:
            self._draw_image()

    def pan_left(self):
        """Pan the image left."""
        if self.tk_image:
            self.pan_offset_x += 20
            self._draw_image()

    def pan_right(self):
        """Pan the image right."""
        if self.tk_image:
            self.pan_offset_x -= 20
            self._draw_image()

    def pan_up(self):
        """Pan the image up."""
        if self.tk_image:
            self.pan_offset_y += 20
            self._draw_image()

    def pan_down(self):
        """Pan the image down."""
        if self.tk_image:
            self.pan_offset_y -= 20
            self._draw_image()

    def zoom_in(self):
        """Increase zoom."""
        if self.image is not None and self.zoom < 5.0:
            self.zoom *= 1.25
            self._update_display_image()
            self._draw_image()
            self.last_roi = None
            self.roi_label.config(text="ROI: (x, y, w, h)")

    def zoom_out(self):
        """Decrease zoom."""
        if self.image is not None and self.zoom > 0.2:
            self.zoom /= 1.25
            self._update_display_image()
            self._draw_image()
            self.last_roi = None
            self.roi_label.config(text="ROI: (x, y, w, h)")

    def reset_view(self):
        """Reset the view (zoom and pan)."""
        if self.image is not None:
            self.zoom = 1.0
            self.pan_offset_x = 0
            self.pan_offset_y = 0
            self._update_display_image()
            self._draw_image()
            self.last_roi = None
            self.roi_label.config(text="ROI: (x, y, w, h)")

    def cancel_selection(self):
        """Cancel the current selection."""
        if self.rect:
            self.canvas.delete(self.rect)
            self.rect = None
            self.last_roi = None
            self.roi_label.config(text="ROI: (x, y, w, h)")

    def on_press(self, event):
        """Handle mouse button press event."""
        if self.display_image is not None and self.tk_image is not None:
            # Check for right click to pan
            if event.num == 3 or event.state & 0x4:  # Right button or Ctrl+left click
                self.is_panning = True
                self.pan_start_x = event.x
                self.pan_start_y = event.y
                self.canvas.config(cursor="fleur")  # Move cursor
            else:  # Left click for selection
                # Compute the actual image position on the canvas
                canvas_width = self.canvas.winfo_width()
                canvas_height = self.canvas.winfo_height()
                img_width = self.tk_image.width()
                img_height = self.tk_image.height()

                # Compute the image position with the pan offset
                img_x = self.pan_offset_x
                img_y = self.pan_offset_y

                # Center the image if it is smaller than the canvas
                if img_width < canvas_width:
                    img_x = (canvas_width - img_width) // 2 + self.pan_offset_x
                if img_height < canvas_height:
                    img_y = (canvas_height - img_height) // 2 + self.pan_offset_y

                # Convert canvas coordinates to image coordinates
                self.start_x = event.x - img_x
                self.start_y = event.y - img_y

                # Check that the click is inside the image
                if 0 <= self.start_x <= img_width and 0 <= self.start_y <= img_height:
                    # Convert back to canvas coordinates to draw the rectangle
                    canvas_start_x = self.start_x + img_x
                    canvas_start_y = self.start_y + img_y
                    self.rect = self.canvas.create_rectangle(canvas_start_x, canvas_start_y, canvas_start_x, canvas_start_y, outline="red", width=2)
                else:
                    self.start_x = self.start_y = None

    def on_drag(self, event):
        """Handle mouse drag event."""
        if self.display_image is not None and self.tk_image is not None:
            if self.is_panning:
                # Pan
                delta_x = event.x - self.pan_start_x
                delta_y = event.y - self.pan_start_y
                self.pan_offset_x += delta_x
                self.pan_offset_y += delta_y
                self.pan_start_x = event.x
                self.pan_start_y = event.y
                self._draw_image()
            elif self.rect and self.start_x is not None and self.start_y is not None:
                # ROI selection
                canvas_width = self.canvas.winfo_width()
                canvas_height = self.canvas.winfo_height()
                img_width = self.tk_image.width()
                img_height = self.tk_image.height()

                # Compute the image position with the pan offset
                img_x = self.pan_offset_x
                img_y = self.pan_offset_y

                # Center the image if it is smaller than the canvas
                if img_width < canvas_width:
                    img_x = (canvas_width - img_width) // 2 + self.pan_offset_x
                if img_height < canvas_height:
                    img_y = (canvas_height - img_height) // 2 + self.pan_offset_y

                # Convert canvas coordinates to image coordinates
                current_x = event.x - img_x
                current_y = event.y - img_y

                # Clamp to the image edges
                current_x = max(0, min(current_x, img_width))
                current_y = max(0, min(current_y, img_height))

                # Convert back to canvas coordinates for drawing
                canvas_start_x = self.start_x + img_x
                canvas_start_y = self.start_y + img_y
                canvas_current_x = current_x + img_x
                canvas_current_y = current_y + img_y

                self.canvas.coords(self.rect, canvas_start_x, canvas_start_y, canvas_current_x, canvas_current_y)

    def on_release(self, event):
        """Handle mouse button release event."""
        if self.is_panning:
            # Finish panning
            self.is_panning = False
            self.canvas.config(cursor="cross")
        elif self.rect and self.start_x is not None and self.start_y is not None and self.display_image is not None and self.tk_image is not None:
            # Finish ROI selection
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            img_width = self.tk_image.width()
            img_height = self.tk_image.height()

            # Compute the image position with the pan offset
            img_x = self.pan_offset_x
            img_y = self.pan_offset_y

            # Center the image if it is smaller than the canvas
            if img_width < canvas_width:
                img_x = (canvas_width - img_width) // 2 + self.pan_offset_x
            if img_height < canvas_height:
                img_y = (canvas_height - img_height) // 2 + self.pan_offset_y

            # Convert canvas coordinates to image coordinates
            end_x = event.x - img_x
            end_y = event.y - img_y

            # Clamp to the image edges
            end_x = max(0, min(end_x, img_width))
            end_y = max(0, min(end_y, img_height))

            # Compute the ROI in original image coordinates
            x1, y1 = min(self.start_x, end_x), min(self.start_y, end_y)
            x2, y2 = max(self.start_x, end_x), max(self.start_y, end_y)
            w, h = x2 - x1, y2 - y1

            # Convert to original image coordinates (without zoom)
            scale = 1.0 / self.zoom
            orig_x1, orig_y1 = int(x1 * scale), int(y1 * scale)
            orig_w, orig_h = int(w * scale), int(h * scale)

            self.roi_label.config(text=f"ROI: ({orig_x1}, {orig_y1}, {orig_w}, {orig_h})")
            self.last_roi = (orig_x1, orig_y1, orig_w, orig_h)

            # Clear temporary variables
            self.start_x = self.start_y = None

    def save_template(self):
        """Save the selected template."""
        if not self.last_roi or self.image is None:
            messagebox.showerror("Error", "Select an ROI first.")
            return

        x, y, w, h = self.last_roi
        roi_img = self.image[y : y + h, x : x + w]

        if roi_img.size == 0:
            messagebox.showerror("Error", "Invalid ROI.")
            return

        os.makedirs(TEMPLATES_DIR, exist_ok=True)

        save_path = filedialog.asksaveasfilename(title="Save template", defaultextension=".png", initialdir=TEMPLATES_DIR, filetypes=[("PNG Images", "*.png")])

        if save_path:
            try:
                cv2.imwrite(save_path, cv2.cvtColor(roi_img, cv2.COLOR_RGB2BGR))
                messagebox.showinfo("Saved", f"Template saved to:\n{save_path}")
                print(f"Template saved: {save_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Error saving template: {str(e)}")

    def save_full_screenshot(self):
        """Save the currently loaded image as a full screenshot."""
        if self.image is None:
            messagebox.showerror("Error", "Load or capture an image first.")
            return

        os.makedirs(TEMPLATES_DIR, exist_ok=True)

        save_path = filedialog.asksaveasfilename(
            title="Save full screenshot",
            defaultextension=".png",
            initialdir=TEMPLATES_DIR,
            filetypes=[("PNG Images", "*.png")],
        )

        if save_path:
            try:
                cv2.imwrite(save_path, cv2.cvtColor(self.image, cv2.COLOR_RGB2BGR))
                messagebox.showinfo("Saved", f"Full screenshot saved to:\n{save_path}")
                print(f"Full screenshot saved: {save_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Error saving screenshot: {str(e)}")

    def copy_roi_coordinates(self):
        """Copy the ROI coordinates to the clipboard."""
        if self.last_roi:
            x, y, w, h = self.last_roi
            coordinates = f"({x}, {y}, {w}, {h})"
            pyperclip.copy(coordinates)
            messagebox.showinfo("Copied", f"ROI coordinates copied to the clipboard:\n{coordinates}")
        else:
            messagebox.showerror("Error", "Select an ROI first.")

    def run(self):
        """Run the application."""
        print("Template Creator started")
        print(f"Templates directory: {TEMPLATES_DIR}")
        self.root.mainloop()


def main():
    """Main function."""
    from wosutil.utils import log_message, setup_logging

    setup_logging()
    log_message("=== Template Creator - WoS Util ===", "info")
    log_message("Application for creating image templates", "info")

    app = TemplateCreatorApp()
    app.run()


if __name__ == "__main__":
    main()
