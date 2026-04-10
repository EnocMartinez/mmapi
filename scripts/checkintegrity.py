import os
from argparse import ArgumentParser
from rich.progress import Progress
import PIL
from PIL import Image
import rich
import numpy as np

def pil_to_numpy(image: Image) -> np.array:
    """
    convert a PIL Image into a np.arrray. Used to decouple inference from the actual file
    """
    image_np = np.array(image)
    # Ensure the image is in RGB format
    if image_np.shape[-1] == 3:  # Only process if it has 3 channels
        # Convert RGB to BGR by reversing the last dimension
        image_bgr = image_np[..., ::-1]
    else:
        raise ValueError("Image does not have 3 channels (RGB).")

    # Ensure the data type is uint8
    image_bgr_uint8 = image_bgr.astype(np.uint8)
    return image_bgr_uint8



def file_list(dir_name) -> list:
    """ create a list of file and sub directories names in the given directory"""
    assert os.path.isdir(dir_name), f"{dir_name} is not a directory!"
    list_of_files = os.listdir(dir_name)
    all_files = list()
    for entry in list_of_files:
        full_path = os.path.join(dir_name, entry)
        if os.path.isdir(full_path):
            all_files = all_files + file_list(full_path)
        else:
            all_files.append(full_path)
    return all_files


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("input", help="Sensor name", type=str)
    args = argparser.parse_args()

files = file_list(args.input)
errors = 0
ignored = 0
ok = 0
corrupt = []


with Progress() as progress:
    task = progress.add_task("task", total=len(files))
    for f in files:
        progress.update(task, advance=1)
        ext = f.split(".")[-1]
        if ext.lower() not in ["jpg", "jpeg", "png"]:
            rich.print(f"[yellow]ignoring non-pic file {f}")
            ignored += 1
            continue

        try:
            img = Image.open(f)
            img.load()
            img.close()

        except PIL.UnidentifiedImageError:
            rich.print(f"[red]Corrupt file: {f}")
            corrupt.append(f)
            errors += 1
            continue
        except OSError:
            rich.print(f"[red]Corrupt file: {f}")
            corrupt.append(f)
            errors += 1
            continue

        ok += 1

rich.print( f"[green]correct {ok} ({100*ok/len(files):.02f} %%)")
rich.print(f"[yellow]ignored {ignored} ({100*ignored/len(files):.02f} %%)")
rich.print(   f"[red]corrupt {errors} ({100*errors/len(files):.02f} %%)")

if errors > 0:

    input("Delete corrupt pictures? ctrl+c to cancel")
    for c in corrupt:
        os.remove(c)








