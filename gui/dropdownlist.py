import tkinter as tk
from tkinter import ttk, W, E, messagebox, filedialog
from .constants import WARN, YELLOW, GREEN, RED, TICK, GREY
from .common_fields import FocusableWidget
import rich


class VocabComboBox(FocusableWidget):
    def __init__(self, root, name, df, row, columnspan=1, padx=10, pady=10, width=60):
        """
        Creates a combobox to select a value from a vocabulary
        :param root:
        :param name:
        :param df:
        :param row:
        :param columnspan:
        :param padx:
        :param pady:
        :param width:
        """
        FocusableWidget.__init__(self)
        self.items = df["prefLabel"].to_list()
        self.name = name
        self.value = tk.StringVar()
        self.root = root
        self.df = df
        # Create a combobox
        tk.Label(self.root, text=name, borderwidth=1).grid(row=row, column=0, padx=padx, pady=pady, sticky=E)
        self.combobox = ttk.Combobox(self.root, textvariable=self.value, width=width)
        self.combobox['values'] = self.items  # Set the initial list
        self.combobox.grid(row=row, column=1, padx=padx, pady=pady, sticky=W)
        self.combobox.bind("<KeyRelease>", self.update_list)  # Trigger filtering as user types
        self.combobox.bind("<KeyPress>", self.validate_list)  # Trigger filtering as user types

        self.combobox.bind("<<ComboboxSelected>>", self.validate_list)  # Handle selection

        self.valtext = tk.Label(self.root, text=WARN, foreground=YELLOW)
        self.valtext.grid(row=row, column=3, padx=padx, pady=pady, sticky=W)

        # self.vocab_label = tk.Label(self.root, text=name)
        # self.vocab_label.grid(row=row + 1, column=0, padx=padx, pady=0, sticky=E)
        self.vocab_code = tk.Label(self.root, text="code:", foreground=GREY)
        self.vocab_code.grid(row=row + 1, column=1, padx=padx, pady=pady, sticky=W)
        self.vocab_uri = tk.Label(self.root, text="uri:", foreground=GREY)
        self.vocab_uri.grid(row=row + 1, column=2, padx=padx, pady=pady, columnspan=520, sticky=W)

    def update_list(self, event):
        """Filter the drop-down list based on user input and open it automatically."""
        typed_text = self.value.get().lower()
        filtered_items = [item for item in self.items if typed_text in item.lower()]

        # Update the combobox's values with the filtered list
        self.combobox['values'] = filtered_items if filtered_items else ["No match found"]

        # Open the drop-down list automatically
        self.combobox.event_generate('<Down>')
        # validate it afterwards
        self.validate_list(event)

        # Return the focus back to the entry field
        self.root.after(100, lambda: self.combobox.focus())


    def on_select(self, event):
        """Handle selection from the drop-down list."""

        self.validate_list(event)

    def validate_list(self, event):

        value = self.value.get()
        df = self.df
        row = df[df["prefLabel"] == value]
        if len(row) == 1:
            code = row["id"].values[0].split("::")[-1]
            uri = row["uri"].values[0]
            self.vocab_uri.config(text=f"uri: {uri}")
            self.vocab_code.config(text=f"code: {code}")
        else:
            self.vocab_uri.config(text=f"uri: not found")
            self.vocab_code.config(text=f"code: not found")

        if not value:
            self.valtext.config(text="", fg=GREEN)  # clear text

        if value in df["prefLabel"].to_list():
            self.valtext.config(text=TICK, fg=GREEN)
        else:
            self.valtext.config(text=WARN, fg=YELLOW)


    def __repr__(self):
        return f"""{self.name}
    label: {self.value.get()}
     code: {self.vocab_code.cget("text")}
      uri: {self.vocab_uri.cget("text")}"""

    def process_label(self, label):
        print(label.cget("text"))
        try:
            l = label.cget("text").split(": ")[1]
        except IndexError:
            return self.value.get()

        if l == "not found":
            return self.value.get()
        return l


    def vocab_lookup(self, d):
        """fills a vocab element based on one of the values"""
        assert(isinstance(d, dict)), f"expected dict, got {type(d)}"

        # Force missing elements
        for e in ["uri", "label", "code"]:
            if e not in d.keys():
                d[e] = ""

        df = self.df
        if d["uri"]:
            row = df[df["uri"] == d["uri"]]
        elif d["label"]:
            row = df[df["prefLabel"] == d["label"]]
        elif d["code"]:
            row = df[df["code"] == d["id"]]
        else:
            raise ValueError("value not found!")

        return {
            "label": row["prefLabel"].values[0],
            "code": row["id"].values[0],
            "uri": row["uri"].values[0]
        }


    def put(self, doc):
        doc = self.vocab_lookup(doc)
        self.value.set(doc["label"])
        self.validate_list(None)

    def get(self):
        return {
            "label": self.value.get(),
            "code": self.process_label(self.vocab_code),
            "uri": self.process_label(self.vocab_uri)
        }


class DropDownList(FocusableWidget):
    def __init__(self, root, name: str, items: list, row: int, padx=10, pady=10, width=60, validate=True):
        """
        Creates a combobox to select a value from a vocabulary
        :param root:
        :param name:
        :param df:
        :param row:
        :param columnspan:
        :param padx:
        :param pady:
        :param width:
        """
        FocusableWidget.__init__(self)
        style = ttk.Style(root)
        style.configure("Normal.TCombobox", fieldbackground="white")
        style.configure("Highlight.TCombobox", fieldbackground="lightyellow")

        self.validate = validate

        self.items = items
        self.name = name
        self.value = tk.StringVar()
        self.root = root
        self.validate = validate
        # Create a combobox
        self.label = tk.Label(self.root, text=name, borderwidth=1, bg=self.root.cget("bg"))
        self.label.grid(row=row, column=0, padx=padx, pady=pady, sticky=E)
        self.combobox = ttk.Combobox(self.root, textvariable=self.value, width=width)
        self.combobox['values'] = self.items  # Set the initial list

        self.combobox.grid(row=row, column=1, padx=padx, pady=pady, sticky=W)
        self.combobox.bind("<KeyRelease>", self.update_list)  # Trigger filtering as user types
        self.combobox.bind("<KeyPress>", self.validate_list)  # Trigger filtering as user types
        self.combobox.bind("<<ComboboxSelected>>", self.validate_list)  # Handle selection
        self.focus_bind(self.combobox)

        if validate:
            self.valtext = tk.Label(self.root, text=WARN, foreground=YELLOW, bg=root.cget("bg"))
            self.valtext.grid(row=row, column=2, padx=padx, pady=pady, sticky=W)
        self.rows = 1

    def focus_in(self, event):
        event.widget.configure(style="Highlight.TCombobox")

    def focus_out(self, event):
        event.widget.configure(style="Normal.TCombobox")


    def update_list(self, event):
        """Filter the drop-down list based on user input and open it automatically."""
        typed_text = self.value.get().lower()
        filtered_items = [item for item in self.items if typed_text in item.lower()]

        # Update the combobox's values with the filtered list
        self.combobox['values'] = filtered_items if filtered_items else ["No match found"]

        # Open the drop-down list automatically
        self.combobox.event_generate('<Down>')
        # validate it afterwards
        self.validate_list(event)

        # Return the focus back to the entry field
        self.root.after(100, lambda: self.combobox.focus())

    def on_select(self, event):
        """Handle selection from the drop-down list."""
        self.validate_list(event)
        self.combobox.config(fg=self.active_bg)
        for obj in self.exclude_objects:
            rich.print(f"{self.name}: Disabling excluded object: {obj.name}")
            obj.disable()

    def validate_list(self, event):
        if not self.validate:
            return
        value = self.value.get()
        if value in self.items:
            self.valtext.config(text=TICK, fg=GREEN)
        else:
            self.valtext.config(text=WARN, fg=RED)

    def put(self, value):
        self.value.set(value)
        self.validate_list(None)

    def get(self):
        return self.value.get()

    def destroy(self):
        self.combobox.destroy()
        if self.validate:
            self.valtext.destroy()



class MultiComboboxMenu:
    def __init__(self, root, name: str, items: list, row: int, padx=10, pady=10, width=60, validate=True):
        self.root = root
        self.name = name
        self.values = items
        self.selected_values = []
        self.var = tk.StringVar(value="Select options")  # Drop-down text

        # Menubutton (acts like a combobox)
        self.menu_button = ttk.Menubutton(self.root, textvariable=self.var, direction="below", width=width)
        self.menu_button.grid(column=0, row=row, sticky="nsew")

        # Create menu for selections
        self.menu = tk.Menu(self.menu_button, tearoff=0)
        self.menu_button["menu"] = self.menu

        self.valid_items = items # list of valid selectable values

        # Add checkbuttons to the menu
        self.check_vars = {}
        for value in items:
            self.check_vars[value] = tk.BooleanVar()
            self.menu.add_checkbutton(
                label=value,
                variable=self.check_vars[value],
                onvalue=True,
                offvalue=False,
                command=self.update_selection
            )
        self.rows = 1

    def update_selection(self):
        """Updates the displayed text based on selected values."""
        self.selected_values = [key for key, var in self.check_vars.items() if var.get()]
        self.var.set(", ".join(self.selected_values) if self.selected_values else "Select options")

    def get(self):
        return self.selected_values

    def put(self, elements):
        self.selected_values = elements
        for e in elements:
            if e not in self.valid_items:
                raise ValueError(f"Element '{e}' is not a valid element ({self.valid_items})")
            self.check_vars[e].set(True)
        self.update_selection()






