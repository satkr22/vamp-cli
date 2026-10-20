from rich.console import Console
from vamp_cli.persistence.event_log import EventLog
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from vamp_cli.config.editor import edit_config
from pathlib import Path
import click
import yaml

def config():
    try:
        # open ~/.vamp/config.yaml
        config = edit_config(Path.home() / ".vamp/config.yaml")
        if config is None:
            print("\nNo changes made.\n")
        else:
            print("\nRestart 'vamp' to load changed configuration.\n")
    
    except yaml.YAMLError as e:
        print(f"Invalid YAML:\n{e}")
    
    except click.ClickException as e:
        click.echo(f"Could not open an editor: {e}")
        click.echo("Set the EDITOR environment variable, e.g. EDITOR=nano")
        
    except Exception as e:
        print("\nConfiguration changes failed.\n\n")