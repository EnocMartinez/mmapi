import logging
import tkinter as tk
from logging import exception
from tkinter import ttk
from tkinter import *
from tkinter import font
from mmm.common import LoggerSuperclass
from gui.common_fields import TextField, IntegerField, FloatField, BoolCheckBox, SimpleTextField, \
    ActivitySelector, DateTime
from gui.dropdownlist import DropDownList, MultiComboboxMenu
import rich
from .api_client import MmapiClient


def comment_options(schema):
    """
    Converts a comment into a set of options. By default use as text, but parse $vocab=<ID> and $style=<style> keys
        this string: "This a plain text $vocab=P06 $style=multicombobox"
    will be converted to
        {"text": This a plain text", "vocab": "p06", "style": "multicombobox"}

    :param comment:
    :return:
    """
    options = {}

    if "$comment" not in schema.keys():
        return {}

    comments = schema["$comment"].split(" ")

    for comment in comments:
        if not comment.startswith("$"):
            continue

        splits = comment.split("$")

        options["text"]  = splits[0]

        for txt in splits[1:]:
            key, value = txt.split("=")
            options[key] = value

    return options

class GenericObject(LoggerSuperclass):
    """
    Generic object to host nested objects in JSON schema (type=object)
    """
    def __init__(self, name, root: tk.Frame, schema, logger, api: MmapiClient, row, column, colour="", label=True, padx=10, pady=10, orientation="vertical"):

        LoggerSuperclass.__init__(self, logger, name)
        self.info(f"Creating object '{name}', padx={padx} pady={pady}")
        self.root = root  # MmapiGuiClient
        self.api = api
        self.name = name
        self.row = row
        self.padx = padx
        self.pady = pady
        self.colour = colour

        assert orientation in ["vertical", "horizontal"]

        self.orientation=orientation

        self.elements = {}  # ALL elements will be stored here, key=name value=TK object

        if not self.colour:  # By default use root's colour
            self.colour = root.cget("bg")

        self.frame = tk.Frame(root, bg=self.colour)  # Metadata frame
        self.frame.grid(column=column, columnspan=3, row=row, sticky="nsew")
        # self.frame.grid_rowconfigure(0, weight=1)
        # self.frame.grid_columnconfigure(0, weight=1)

        crow = 0
        if label:
            tk.Label(self.frame, text=name, bg=self.colour).grid(row=0, column=0, sticky="nsew")
            separator = ttk.Separator(self.frame, orient='horizontal')
            separator.grid(row=0, column=1, columnspan=2, sticky="ew", padx=3, pady=3)
            crow += 1
        # else:
        #     separator = ttk.Separator(self.frame, orient='horizontal')
        #     separator.grid(row=0, column=0, columnspan=3, sticky="ew", padx=3, pady=1)
        #     crow += 1

        self.schema2gui(schema, start_row=crow)
        self.rows = 1  # All data here is included in a single frame

    def put(self, doc):
        for key, value in doc.items():
            if key not in self.elements.keys():
                self.warning(f"Element '{key}' not defined in schema!")
            else:
                self.elements[key].put(value)

    def get(self):
        data = {}
        for key, element in self.elements.items():
            data[key] = element.get()
        return data

    def destroy(self):
        self.frame.destroy()

    def schema2gui(self, schema, start_row=0) -> int:
        """
        Converts a JSON schema into a GUI element
        :param schema: JSON schema
        :param start_row: initial row
        :return: rows
        """
        frame = self.frame

        if self.orientation == "horizontal":
            obj_frame = tk.Frame(frame, bg=frame.cget("bg"))
            obj_frame.grid(row=start_row, column=0, columns=3, padx=self.padx, pady=self.pady)
            row = 0
            column = 0
            padx = 2
            pady = self.pady
            width = 15

        else:
            padx = self.padx
            pady = self.pady
            row = start_row
            width = 50

        if schema["type"] == "string":
            options = comment_options(schema)
            if "style" in options.keys() and options["style"] == "DateTime":
                e = DateTime()
            else:
                e = self.process_string(schema, frame, "", row)

        elif schema["type"] == "object":
            options = comment_options(schema)
            for name, props  in schema["properties"].items():
                if self.orientation == "horizontal":
                    # Create a new frame
                    self.debug(f"Creating frame for {name}")
                    frame = tk.Frame(obj_frame, bg=self.colour)
                    frame.grid(column=column, row=0)
                    row = 0
                    width = 30
                else:
                    width = 60
                e = None
                if "oneOf" in props.keys():
                    e = OneOf(frame, name, props["oneOf"], self.logger, self.api, row)
                    row += 1
                else:
                    if props["type"] == "string":
                        options = comment_options(props)

                        if "width" in options.keys():
                            width = options["width"]

                        if "style" in options.keys() and options["style"] == "DateTime":
                            self.debug(f"Processing '{name}' as DateTime")
                            e = DateTime(frame, name, row=row)
                        else:
                            rich.print(f"[green]Processing '{name}' as string with width={width}")
                            e = self.process_string(props, frame, name, row, width=width)
                        row += e.rows

                    elif props["type"] == "integer":
                        e = IntegerField(frame, name, row, padx=padx, pady=pady)

                    elif props["type"] == "number":
                        e = FloatField(frame, name, row, padx=padx, pady=pady)

                    elif props["type"] == "boolean":
                        e = BoolCheckBox(name, frame, row, padx=padx, pady=pady)

                    elif props["type"] == "object":
                        self.debug(f"Processing {name} as Nested Object")
                        e = GenericObject(name, frame, props, self.logger, self.api, row, 0, padx=padx, pady=pady)

                    elif props["type"] == "array":
                        options = comment_options(props)
                        if "style" in options.keys() and options["style"] == "multicombobox":
                            e = MultiComboboxMenu(frame, name, props["items"]["enum"], row, padx=padx, pady=pady, width=width)
                        elif "style" in options.keys() and options["style"] == "ActivitySelector":
                            e = ActivitySelector(frame, name, self.api, row, self.logger, padx=padx, pady=pady, width=width)
                        else:
                            self.debug(f"Processing '{name}' as Nested Object Array")
                            e = GenericArray(name, frame, props, self.logger, self.api, row, padx=padx, pady=pady)

                    if not e:
                        self.error(f"Element {name} could not be processed!", exception=ValueError)

                    if self.orientation == "horizontal":
                        column += 1
                    else:  # vertical
                        row += e.rows
                self.elements[name] = e  # Store the new element in the global element list

        else:
            rich.print(schema)
            raise ValueError("Invalid schema!!")

        return row

    def process_string(self, schema, frame, name, row, padx=None, pady=None, width=60):
        options = {}
        options = comment_options(schema)

        if not padx:
            padx = self.padx
        if not pady:
            pady = self.pady

        if name.startswith("@"):
            items = self.api.get_ids(name[1:])
            e = DropDownList(frame, name, items, row, padx=padx, pady=pady, width=width)

        elif "vocab" in options.keys():
            self.info("Using Vocab!")
            self.warning(f"UNIMPLEMENTED Processing {name} as VocabComboBox")
            e = TextField(frame, name, row, padx=padx, pady=pady, width=width)
        elif "enum" in schema.keys():
            self.debug(f"Processing {name} as ComboBox")
            if "style" in options.keys() and options["style"] == "multicombobox":
                e = MultiComboboxMenu(frame, name, schema["enum"], row, padx=padx, pady=pady, width=width)
            else:
                e = DropDownList(frame, name, schema["enum"], row, padx=padx, pady=pady, width=width)
        elif "time" == name.lower():
            self.debug(f"Processing {name} as TimeField")
            e = TextField(frame, name, row, padx=padx, pady=pady, validation="time")
        else:
            self.debug(f"Processing {name} as TextField")
            e = TextField(frame, name, row, padx=padx, pady=pady, width=width)
        return e



class GenericArray(LoggerSuperclass):
    """
    Generic object to host Array of nested objects in JSON schema (type=object)
    """
    def __init__(self, name: str, root: tk.Frame, schema: dict, logger: logging.Logger, api: MmapiClient, row: int,
                 padx:int=10, pady:int=10):
        LoggerSuperclass.__init__(self, logger, name)
        self.root = root  # MmapiGuiClient
        self.api = api
        self.name = name
        self.row = row
        self.padx = padx
        self.pady = pady

        # Convert from Array schema to Object schema
        self.schema = schema
        self.colour = root.cget("bg")
        outrow = row  # root row, expected to have 3 cols

        # ----- line  (3 cols)
        # ----- frame (3 cols)
        #     | name | + btn | - btn |
        #     |    <array frame >    |
        # ----- line  (3 cols)

        separator_top = ttk.Separator(root, orient='horizontal')
        separator_top.grid(row=outrow, column=0, columnspan=3, sticky="ew", padx=3, pady=3)  # Top separator
        self.frame = tk.Frame(root, bg=self.colour)
        self.frame.grid(column=0, columnspan=3, row=outrow+1, sticky="nsew")  # frame to host everything
        separator_btm = ttk.Separator(root, orient='horizontal')
        separator_btm.grid(row=outrow+2, column=0, columnspan=3, sticky="ew", padx=3, pady=3)
        outrow += 3
        self.rows = 3

        self.row = 0
        label = tk.Label(self.frame, text=name, bg=self.colour)  # Name
        label.grid(row=self.row, column=0, sticky="nsew", padx=20, pady=pady)

        btn_add_row = tk.Button(self.frame, text="+ Add Element", command=self.add_row, bg=self.colour)
        btn_add_row.grid(row=self.row, column=1,  padx=20, pady=3, sticky=tk.W)

        btn_del_row = tk.Button(self.frame, text="- Delete Element", command=self.delete_row, bg=self.colour)
        btn_del_row.grid(row=self.row, column=2, padx=20, pady=pady, sticky=tk.W)

        self.row += 1
        self.array_frame = tk.Frame(self.frame, bg=self.colour)
        self.array_frame.grid(column=0, columnspan=3, row=self.row, sticky="nsew")  # frame to host everything
        self.row += 1

        self.array_elements = []
        # By default, create one array to tip the geometry manager
        # self.add_row()

    def add_row(self):
        name = f"{self.name}-{len(self.array_elements)}"
        # Converting from array type to object type
        e = self.schema2gui(self.schema)
        self.array_elements.append(e)
        return e

    def delete_row(self):
        widget = self.root.focus_get()
        rich.print(f"selected element '{id(widget)}' type {type(widget)}")

        index_to_be_deleted = -1
        # Loop through the array elements and access its value based on its main data type (text, Combobox, etc.)
        # Mark the index of the element currently being selected
        for i, a in enumerate(self.array_elements):
            if isinstance(a, DropDownList):
                if id(a.combobox) == id(widget):
                    index_to_be_deleted = i
                    break
            else:
                self.error(f"Unimplemented type {type(a)}")

        if index_to_be_deleted < 0:
            self.error(f"Selected activity not found in list!")
            return

        rich.print(f"Data Before {self.get()}")
        new_array = []
        data = []

        for i, e in enumerate(self.array_elements):
            if i != index_to_be_deleted:
                new_array.append(e)
                data.append(e.get())

        for e in self.array_elements:
            self.row -= e.rows
            e.destroy()

        self.array_elements = []
        for d in data:
            e = self.add_row()
            e.put(d)



    def put(self, data_array: list):
        if len(self.array_elements):
            self.error(f"Array '{self.name}' already initialized!", exception=ValueError)

        for _ in range(len(data_array)):
            self.add_row()

        for element, data_in in zip(self.array_elements, data_array):
            element.put(data_in)

    def get(self) -> list:
        values = []
        for e in self.array_elements:
            values.append(e.get())
        return values

    def schema2gui(self, schema, width=40):
        """
        Converts a JSON schema into a GUI element
        """
        options = comment_options(schema)
        if "width" in options.keys():
            width = options["width"]
        rich.print(f"[magenta]creating {self.name}")
        frame = self.array_frame
        if len(schema["items"]) == 1:
            # only one element in the array
            if schema["items"]["type"] == "string":
                if "width" in options.keys():
                    width = options["width"]
                #e = SimpleTextField(frame, self.row, padx=self.padx, pady=2)
                rich.print(f"[blue]Process string '{self.name}' with width={width}")
                e = self.process_string(schema["items"], frame, self.name, self.row, width=width)
            else:
                self.error("Unimplemented!", exception=ValueError)
        else:
            self.info("Creating Array")
            e = GenericObject("array", frame, schema["items"], self.logger, self.api, self.row, 0,
                              padx=self.padx, pady=1, label=False, orientation="horizontal")
        self.row += e.rows
        e.parent_object = self
        return e

    def process_string(self, schema, frame, name, row, padx=None, pady=None, width=30):
        rich.print(f"[blue]Array string {name}")
        options = comment_options(schema)
        if "width" in options.keys():
            width=options["width"]

        if not padx:
            padx = self.padx
        if not pady:
            pady = self.pady

        if name.startswith("@"):
            items = self.api.get_ids(name[1:])
            e = DropDownList(frame, "", items, row, padx=padx, pady=pady, width=width)
        elif "$comment" in schema.keys() and "VOCAB" in schema["$comment"]:
            self.info("Using Vocab!")
            self.warning(f"UNIMPLEMENTED Processing {name} as VocabComboBox")
            e = TextField(frame, name, row, padx=padx, pady=pady, width=width)
        elif "enum" in schema.keys():
            self.debug(f"Processing {name} as ComboBox")
            e = DropDownList(frame, name, schema["enum"], row, padx=padx, pady=pady, width=width, validate=False)
        elif "time" == name.lower():
            self.debug(f"Processing {name} as TimeField")
            e = TextField(frame, name, row, padx=padx, pady=pady, validation="time")
        else:
            rich.print(f"[cyan]Processing {name} as TextField widht={width}")
            e = TextField(frame, name, row, padx=padx, pady=pady, width=width)
        return e


class EmbeddedScrollableFrame(tk.Frame):
    def __init__(self, parent, frame_width=300, frame_height=500, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        # Canvas and Scrollbar
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.vscrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscrollbar.set)

        # Layout
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Create an inner frame inside the canvas
        self.inner_frame = tk.Frame(self.canvas, width=frame_width, height=frame_height, bg="blue")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        # Bind resizing events
        self.inner_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse Wheel Binding
        self.inner_frame.bind("<Enter>", self._bind_mousewheel)
        self.inner_frame.bind("<Leave>", self._unbind_mousewheel)

    def _on_frame_configure(self, event):
        """Update scroll region to include the entire inner frame."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Resize the inner frame dynamically to match canvas width."""
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Enable mouse wheel scrolling."""
        system = self.winfo_toplevel().tk.call("tk", "windowingsystem")
        if system == "win32":
            self.canvas.yview_scroll(-1 * int(event.delta / 120), "units")
        elif system == "aqua":
            self.canvas.yview_scroll(-1 * int(event.delta), "units")
        else:
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")

    def _bind_mousewheel(self, event):
        """Bind mouse wheel scrolling."""
        system = self.winfo_toplevel().tk.call("tk", "windowingsystem")
        if system in ("win32", "aqua"):
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        else:
            self.canvas.bind_all("<Button-4>", self._on_mousewheel)
            self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        """Unbind mouse wheel scrolling when leaving."""
        system = self.winfo_toplevel().tk.call("tk", "windowingsystem")
        if system in ("win32", "aqua"):
            self.canvas.unbind_all("<MouseWheel>")
        else:
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")


class OneOf(LoggerSuperclass):
    def __init__(self, root, name, conf: list, logger, api, row, padx=10, pady=10):
        LoggerSuperclass.__init__(self, logger, f"oneOf-{name}")
        self.padx = padx
        self.pady = pady
        self.name = name
        frame = tk.Frame(root, bg=root.cget("bg"))
        total_columns = root.grid_size()[0]  # grid_size() returns (columns, rows)
        frame.grid(row=row, column=0, columns=total_columns, padx=self.padx, pady=self.pady, sticky="nsew")
        self.keys = []
        self.conf = {}
        self.logger = logger
        self.api = api
        for element in conf:
            key = list(element["properties"].keys())[0]
            self.conf[key] = element
            self.keys.append(key)

        total_columns = 2*len(self.conf.keys()) + 1

        column = 0
        row = 0
        separator_top = ttk.Separator(frame, orient='horizontal')
        separator_top.grid(row=row, column=0, columnspan=total_columns, sticky="ew", padx=3, pady=3)
        row += 1
        tk.Label(frame, text=name + ":", bg=frame.cget("bg"), font=("TkDefaultFont", 10, "bold")).grid(column=column, row=row, pady=pady, padx=padx, sticky=W)
        column += 1
        self.boxes = {}

        for key in self.conf.keys():
            var = tk.IntVar()
            self.boxes[key] = var
            btn = tk.Checkbutton(frame, variable=var, bg=frame.cget("bg"),  command=lambda iden=key: self.on_checkbox_toggle(iden))
            btn.grid(column=column, row=row)
            lbl = tk.Label(frame, text=key, bg=frame.cget("bg"))
            lbl.grid(column=column + 1, row=row, pady=pady, padx=padx, sticky=W)
            column += 2

        row += 1
        self.frame = tk.Frame(frame, bg=frame.cget("bg"))
        total_columns = frame.grid_size()[0]
        self.frame.grid(column=0, row=row, columnspan=total_columns)
        row += 1

        sep = ttk.Separator(self.frame, orient='horizontal')
        sep.grid(row=row, column=0, columnspan=total_columns, padx=3, pady=3, sticky="ew")
        self.object = None
        self.currently_selected = ""
        self.rows = 1

    def on_checkbox_toggle(self, checkbox_key):
        print(f"Selected {checkbox_key} - value : {self.boxes[checkbox_key].get()}")
        value = self.boxes[checkbox_key].get()
        selected_key = ""
        if value == 1:
            for key, check in self.boxes.items():
                if key == checkbox_key:
                    # Create a new Object
                    selected_key = key
                else:
                    check.set(0)
                    if self.object:
                        self.object.destroy()
            self.object = GenericObject(selected_key, self.frame, self.conf[selected_key], self.logger, self.api,
                                        column=0, row=0, label=False, colour=self.frame.cget("bg")) # , orientation="horizontal")

        elif value == 0:
            self.object.destroy()

    def get(self):
        if self.object:
            return {self.currently_selected: self.object.get()}
        else:
            return {}

    def put(self, data):
        key = list(data.keys())[0]
        self.boxes[key].set(1)
        self.on_checkbox_toggle(key)
        self.object.put(data)

