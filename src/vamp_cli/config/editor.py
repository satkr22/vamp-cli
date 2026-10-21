import yaml
import click
from pathlib import Path


def edit_config(path: str | Path) -> str | None:
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    while True:
        try:
            edited = click.edit(text, extension=".yaml")  # blocks until editor closes
            if edited is None:  # user quit without saving
                return None
            
            try:
                data = yaml.safe_load(edited)
            except yaml.YAMLError as e:
                click.echo(f"Invalid YAML:\n{e}")
                if not click.confirm("Re-open editor?", default=True):
                    return None
                text = edited  # keep changes so can be fixed
                continue
            
            path.write_text(edited, encoding="utf-8")  # only save once valid
            return "ok"
        
        except click.ClickException as e:
            raise 
    