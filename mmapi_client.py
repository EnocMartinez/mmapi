from argparse import ArgumentParser
from gui import MmapiGui
import yaml
from mmm.common import setup_log





if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    args = argparser.parse_args()
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    log = setup_log("GUI")
    gui = MmapiGui(secrets, log)
    gui.run()
