import os
import json
import configparser
from datetime import datetime
import shutil
from pathlib import Path

from openai import AsyncOpenAI, APIError
from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal, Grid
from textual.screen import Screen, ModalScreen
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    Button,
    Markdown,
    ListView,
    ListItem,
    Label,
    Input,
    Static,
)
from textual.message import Message

def get_config_path():
    return Path.home() / ".config" / "batch_manager" / "config.ini"

CONFIG_FILE = get_config_path()
BATCH_HEADERS = ["ID", "Status", "Created At"]
# For file listing, show filename (id), size, purpose, created
FILE_HEADERS = ["Filename", "Size", "Purpose", "Created At"]
DOWNLOAD_DIR = "downloads"


def human_readable_mb(bytes_size: int) -> str:
    """
    Convert bytes to human-readable megabytes string.
    """
    mb = bytes_size / (1024 * 1024)
    return f"{mb:.0f} MB"

def human_readable_kb(bytes_size: int) -> str:
    """
    Convert bytes to human-readable megabytes string.
    """
    kb = bytes_size / (1024)
    return f"{kb:.0f} KB"

def human_readable_bytes(bytes_size: int) -> str:
    """
    Convert bytes to human-readable bytes string.
    """
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{human_readable_kb(bytes_size)}"
    else:
        return f"{human_readable_mb(bytes_size)}"

class ProfileSelected(Message):
    """
    Message posted when a user selects an API profile.
    Carries the profile name and api_key.
    """
    def __init__(self, profile_name: str, api_key: str) -> None:
        self.profile_name = profile_name
        self.api_key = api_key
        super().__init__()

class ConfirmDeleteFile(ModalScreen[bool]):
    """
    Confirmation modal screen for deleting a file.
    """
    def __init__(self, file_id: str, filename: str):
        super().__init__()
        self.file_id = file_id
        self.filename = filename
        
    def compose(self) -> ComposeResult:
        yield Grid(
            Label(f"Are you sure you would like to delete the file {self.filename} ({self.file_id})?", id="question"),
            Horizontal(
                Button("Delete file", variant="error", id="delete"),
                Button("Cancel", variant="primary", id="cancel"),
                id="dialog-button-bar"),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete":
            self.dismiss(True)
        else:
            self.dismiss(False)

class KeySelectionScreen(Screen):
    """
    Screen to choose an API key profile from config.ini.
    """
    def compose(self) -> ComposeResult:
        yield Header(name="Select API Key Profile")
        yield Vertical(
            Label("Loading profiles...", id="status-label"),
            ListView(id="profile-list"),
            id="selection-container"
        )
        yield Footer()

    def on_mount(self) -> None:
        config = configparser.ConfigParser()
        list_view = self.query_one(ListView)
        status_label = self.query_one("#status-label", Label)

        if not os.path.exists(CONFIG_FILE):
            status_label.update(
                f"[b]Error:[/b] '{CONFIG_FILE}' not found.\nPlease create it and add your API keys."
            )
            return

        config.read(CONFIG_FILE)
        self.profiles = config.sections()

        if not self.profiles:
            status_label.update(
                f"[b]Error:[/b] No profiles found in '{CONFIG_FILE}'."
            )
            return

        status_label.update(
            "Select a profile using arrow keys and press [b]Enter[/b]."
        )

        self.config = config
        for profile in self.profiles:
            list_view.append(ListItem(Label(profile)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        profile_name = event.item.query_one(Label).renderable
        try:
            api_key = self.config[str(profile_name)]["api_key"]
            if not api_key or not api_key.startswith("sk-"):
                raise KeyError
            self.post_message(ProfileSelected(profile_name, api_key))
        except (AttributeError, KeyError):
            self.query_one("#status-label", Label).update(
                f"[b]Error:[/b] Invalid or missing 'api_key' in profile '{profile_name}'."
            )

class FileBrowserModal(ModalScreen[str]):
    """ Modal screen for browsing and selecting files to upload."""
    def __init__(self, start_path: str = "."):
        super().__init__()
        self.current_path = Path(start_path).resolve()
        self.filename = ""
    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Select file to upload."),
            ListView(id="file-list"),
            Horizontal(
                Button("Refresh", id="refresh", variant="success"),
                Button("Upload", id="upload", variant="primary"),
                Button("Cancel", id="cancel", variant="error"),
                id="dialog-button-bar"
            ),
            id="dialog_upload"
        )
    
    def on_mount(self):
        self.refresh_file_list()

    def refresh_file_list(self):
        file_list = self.query_one("#file-list", ListView)
        file_list.clear()
        # move up to parent directory
        if self.current_path.parent != self.current_path:
            file_list.append(ListItem(Label(".. (upper)", id="up")))
        # list current directory contents
        for entry in sorted(self.current_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.is_dir():
                file_list.append(ListItem(Label(f"> {entry.name}", id="dir")))
            else:
                file_list.append(ListItem(Label(entry.name, id="file")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        label = event.item.query_one(Label)
        id_ = label.id
        text = label.renderable
        if id_ == "up":
            self.current_path = self.current_path.parent
            self.refresh_file_list()
        elif id_ == "dir":
            dirname = text.replace("> ", "")
            self.current_path = self.current_path / dirname
            self.refresh_file_list()
        elif id_ == "file":
            self.filename = text
            
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss("")
        elif event.button.id == "refresh":
            self.refresh_file_list()
        elif event.button.id == "upload":
            self.notify(f"Selected file: {self.filename}", title="File Selected")
            self.dismiss(str(self.current_path / self.filename))


class CreateBatchModal(ModalScreen[str]):
    """Modal to collect batch creation parameters: select endpoint and optional input file from available files.
    The modal accepts an optional `files` list of tuples (file_id, filename).
    """
    def __init__(self, files=None):
        super().__init__()
        self.files = files or []
        self.selected_endpoint = None
        self.selected_file_id = ""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Create a new batch", id="create-title"),
            Label("Choose endpoint:", id="label-endpoint"),
            ListView(id="endpoint-list"),
            Label("Select input file:", id="label-input"),
            ListView(id="files-list"),
            Horizontal(
                Button("Create", id="create", variant="primary"),
                Button("Cancel", id="cancel", variant="error"),
                id="dialog-button-bar",
            ),
            id="dialog_upload",
        )

    def on_mount(self) -> None:
        # populate endpoint list
        endpoint_list = self.query_one("#endpoint-list", ListView)
        endpoint_list.clear()
        for ep in ["/v1/responses", "/v1/moderations", "/v1/chat/completions", "/v1/embeddings", "/v1/completions"]:
            endpoint_list.append(ListItem(Label(ep)))

        # populate files list
        files_list = self.query_one("#files-list", ListView)
        files_list.clear()
        files_list.append(ListItem(Label("<none>", id="none")))
        for fid, fname in self.files:
            # display filename but set id to file id via Label id
            files_list.append(ListItem(Label(fname, id=str(fid))))
        # focus endpoint list
        try:
            self.query_one("#endpoint-list", ListView).focus()
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # determine which list sent the event
        lst = event.item.parent
        label = event.item.query_one(Label)
        text = label.renderable
        lid = label.id
        if lst.id == "endpoint-list":
            self.selected_endpoint = text
        elif lst.id == "files-list":
            if lid == "none":
                self.selected_file_id = ""
            else:
                self.selected_file_id = lid

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss("")
        elif event.button.id == "create":
            endpoint = self.selected_endpoint or "/responses"
            fileid = self.selected_file_id or ""
            self.dismiss(f"{endpoint}||{fileid}")

class BatchManagerScreen(Screen):
    """
    Main batch and file management screen, active after API key is selected.
    """
    def __init__(self, api_key: str, profile_name: str):
        super().__init__()
        self.client = AsyncOpenAI(api_key=api_key)
        self.profile_name = profile_name
        self.table_mode = "batches"
        self.current_output_file_id = None
        self.current_file_name = None
        self.cached_files: list[tuple[str, str]] = []
        self.current_batch_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(name="Batch & File Manager")
        with Horizontal(id="main-container"):
            with Vertical(id="left-pane"):
                yield Static(f"[b]{self.profile_name}[/b]", id="profile-panel")
                with Horizontal(id="left-button-bar"):
                    yield Button("List Batches", id="btn-list-batches", variant="primary")
                    yield Button("List Files", id="btn-list-files", variant="default")
                    yield Button("Change Key", id="btn-change-key", variant="warning")
                    yield Button("Refresh", id="btn-refresh", variant="success", disabled=False)
                yield DataTable(id="batch-table", cursor_type="row")
            with Vertical(id="right-pane"):
                with Horizontal(id="right-button-bar"):
                    yield Button("Download", id="btn-download", variant="success", disabled=True)
                    yield Button("Delete", id="btn-delete", variant="error", disabled=True)
                    yield Button("Create", id="btn-action", variant="primary", disabled=False)
                yield Markdown("Select an item to view details.", id="details-view")
                yield Button("Cancel Batch", id="btn-cancel-batch", variant="warning", disabled=True)
        yield Footer()

    def update_action_button(self):
        btn = self.query_one("#btn-action", Button)
        if self.table_mode == "batches":
            btn.label = "Create"
        else:
            btn.label = "Upload"

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*BATCH_HEADERS)
        self.action_list_batches()

    def action_list_batches(self) -> None:
        self.notify("Loading batches...", title="Fetch")
        self.table_mode = "batches"
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns(*BATCH_HEADERS)
        # Batches should not allow delete
        btn = self.query_one("#btn-delete", Button)
        btn.disabled = True
        # cancel batch button should start disabled until a batch is selected
        try:
            self.query_one("#btn-cancel-batch", Button).disabled = True
            self.current_batch_id = None
        except Exception:
            pass
        self.update_action_button()
        self.run_worker(self.list_batches_worker(), exclusive=True)

    async def list_batches_worker(self) -> None:
        try:
            resp = await self.client.batches.list(limit=30)
            table = self.query_one(DataTable)
            for b in resp.data:
                created = datetime.fromtimestamp(b.created_at).strftime("%Y-%m-%d %H:%M")
                table.add_row(b.id, b.status, created, key=b.id)
        except APIError as e:
            self.notify(f"API Error: {e}", severity="error")

    async def cancel_batch_worker(self):
        """Cancel the currently-selected batch."""
        batch_id = self.current_batch_id
        if not batch_id:
            self.notify("No batch selected to cancel.", severity="error")
            return
        self.notify(f"Cancelling batch {batch_id}...", title="Cancel")
        try:
            resp = await self.client.batches.cancel(batch_id)
            # some SDKs return an object; otherwise assume success if no exception
            self.notify(f"Cancel requested for {batch_id}", title="Cancel", timeout=3)
            # refresh list and disable cancel button
            self.action_list_batches()
            try:
                self.query_one("#btn-cancel-batch", Button).disabled = True
            except Exception:
                pass
            self.current_batch_id = None
        except APIError as e:
            self.notify(f"API Error: {e}", severity="error")
        except Exception as e:
            self.notify(f"Error cancelling batch: {e}", severity="error")

    def action_list_files(self) -> None:
        self.notify("Loading files...", title="Fetch", timeout=1)
        self.table_mode = "files"
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns(*FILE_HEADERS)
        self.update_action_button()
        self.query_one("#details-view", Markdown).update("Select an item to view details.")
        # when listing files, cancel batch button should be disabled
        try:
            self.query_one("#btn-cancel-batch", Button).disabled = True
            self.current_batch_id = None
        except Exception:
            pass
        self.run_worker(self.list_files_worker(), exclusive=True)

    async def list_files_worker(self) -> None:
        try:
            resp = await self.client.files.list()
            table = self.query_one(DataTable)
            # cache files for reuse in create modal
            self.cached_files = []
            for f in resp.data:
                created = datetime.fromtimestamp(f.created_at).strftime("%Y-%m-%d %H:%M")
                size_h = human_readable_bytes(f.bytes)
                display_name = f.filename or "<no-name>"
                self.cached_files.append((f.id, display_name))
                table.add_row(f"{display_name}", size_h, f.purpose, created, key=f.id)
        except APIError as e:
            self.notify(f"API Error: {e}", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "btn-list-batches":
            self.action_list_batches()
        elif btn == "btn-list-files":
            self.action_list_files()
        elif btn == "btn-change-key":
            self.app.push_screen(KeySelectionScreen())
        elif btn == "btn-download" and self.current_output_file_id:
            self.run_worker(self.download_output_worker(), exclusive=True)
        elif btn == "btn-delete" and self.current_output_file_id:
            self.app.push_screen(
                ConfirmDeleteFile(self.current_output_file_id, self.current_file_name),
                self.delete_file_worker
            )
        elif btn == "btn-cancel-batch":
            # cancel current batch
            if self.current_batch_id:
                self.run_worker(self.cancel_batch_worker(), exclusive=True)
            else:
                self.notify("No batch selected to cancel.", severity="error")
                
        elif btn == "btn-action":
            if self.table_mode == "batches":
                # Fetch available files and open create-batch modal
                self.run_worker(self.open_create_modal_worker(), exclusive=True)
            else:
                # Upload file
                self.app.push_screen(FileBrowserModal(), self.upload_file_worker)
        elif btn == "btn-refresh":
            # refresh current view
            if self.table_mode == "batches":
                self.action_list_batches()
            else:
                self.action_list_files()
    
    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if self.table_mode == "batches":
            self.run_worker(self.retrieve_batch_worker(key), exclusive=True)
        else:
            self.run_worker(self.retrieve_file_worker(key), exclusive=True)

    async def retrieve_batch_worker(self, batch_id: str):
        # self.notify(f"Fetching batch {batch_id}...", title="Details")
        try:
            b = await self.client.batches.retrieve(batch_id)
            # fetch input/output filenames
            input_name = None
            output_name = None
            try:
                if b.input_file_id:
                    meta = await self.client.files.retrieve(b.input_file_id)
                    input_name = meta.filename
            except:
                input_name = None
            try:
                if b.output_file_id:
                    meta = await self.client.files.retrieve(b.output_file_id)
                    output_name = meta.filename
            except:
                output_name = None
            err = b.errors.data[0].message if b.errors and b.errors.data else "None"
            # timestamp parsing
            stamp_list = ["created_at", "in_progress_at",
                          "finalizing_at", "completed_at", "failed_at",
                          "expired_at", "cancelling_at", "cancelled_at"
                        ]
            
            timestamps = []
            for ts in stamp_list:
                if getattr(b, ts) is not None:
                    timestamps.append({
                        "name": ts.replace("_at", " ").strip(),
                        "time": getattr(b, ts),
                        "value": datetime.fromtimestamp(getattr(b, ts)).strftime("%Y-%m-%d %H:%M")
                    })
            timestamps.sort(key=lambda x: x["time"])
            text_timestamp = "\n".join(
                [f"- {ts['value']} -> {ts['name']}" for ts in timestamps]
            )
            md = f"""
# BATCH 
`{b.id}`

---
- **Status**: {b.status}
- **Endpoint**: {b.endpoint}

**Requests**: {b.request_counts.completed}/{b.request_counts.total} (failed {b.request_counts.failed})

📂 **Files**:
- Input: {b.input_file_id}
  - File Name: {input_name or 'N/A'}
- Output: {b.output_file_id or 'N/A'}
  - File Name: {output_name or 'N/A'}

**Errors**: {err}

---
⌚ **Timestamps**:
{text_timestamp}
"""
            self.query_one("#details-view", Markdown).update(md)
            btn = self.query_one("#btn-download", Button)
            if b.output_file_id:
                btn.disabled = False
                self.current_output_file_id = b.output_file_id
                self.current_file_name = output_name
            else:
                btn.disabled = True
                self.current_output_file_id = None
                self.current_file_name = None
            # enable cancel button for this batch
            try:
                self.query_one("#btn-cancel-batch", Button).disabled = False
                self.current_batch_id = b.id
            except Exception:
                self.current_batch_id = None
        except APIError as e:
            self.notify(f"API Error: {e}", severity="error")

    async def retrieve_file_worker(self, file_id: str):
        # self.notify(f"Fetching file {file_id}...", title="Details")
        try:
            f = await self.client.files.retrieve(file_id)
            created = datetime.fromtimestamp(f.created_at).strftime("%Y-%m-%d %p %I:%M")
            size_h = human_readable_bytes(f.bytes)
            status = "✅ Ready"
            md = f"""
# FILE

**{f.filename or file_id}**

- Status: {status}
- File ID: {f.id}
- Purpose: {f.purpose}
- Size: {size_h}
- Created at: {created}
"""
            self.query_one("#details-view", Markdown).update(md)
            btn = self.query_one("#btn-download", Button)
            btn.disabled = False
            btn = self.query_one("#btn-delete", Button)
            btn.disabled = False
            self.current_output_file_id = file_id
            self.current_file_name = f.filename or file_id
        except APIError as e:
            self.notify(f"API Error: {e}", severity="error")

    async def download_output_worker(self):
        file_id = self.current_output_file_id
        file_name = self.current_file_name or file_id
        if not file_id:
            return
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        safe_name = file_name.replace("/", "_")
        out_path = os.path.join(DOWNLOAD_DIR, f"{safe_name}")
        if not out_path.endswith('.jsonl'):
            out_path += '.jsonl'
        
        self.notify(f"Downloading {file_name}...", title="Download")
        try:
            resp = await self.client.files.content(file_id)
            raw = resp.content.decode()
            lines = raw.strip().split("\n")
            objs = [json.loads(l) for l in lines]
            with open(out_path, "w", encoding="utf-8") as f:
                for obj in objs:
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.notify(f"Saved to {out_path}", title="Done")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    async def delete_file_worker(self, confirmed: bool):
        '''
        Delete the currently selected file.
        '''
        if not confirmed:
            self.notify("File deletion cancelled.", title="Delete")
            return

        file_id = self.current_output_file_id
        file_name = self.current_file_name or file_id  
        if not file_id:
            self.notify(f"File ID is not set.", severity="error")
            return

        try:
            resp = await self.client.files.delete(file_id)
            if resp.deleted:
                self.notify(f"File {file_name} deleted successfully.", title="Delete")
            else:
                self.notify(f"Failed to delete file {file_name}.", severity="error")
            self.action_list_files()
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    async def upload_file_worker(self, file_path: str):
        '''
        Upload a file to OpenAI storage.
        '''
        if file_path == "":
            return
        try:
            await self.client.files.create(
                file=open(file_path, "rb"),
                purpose="batch",
            )
            self.notify(f"File \n{file_path} uploaded successfully.", title="Upload", timeout=5)
            self.action_list_files() # for refresh
        except Exception as e:
            self.notify(f"Error uploading file: {e}", severity="error")
    
    async def create_batch_worker(self, payload: str):
        """
        Create a new batch using the provided payload returned from the CreateBatchModal.
        Payload format: "<endpoint>||<input_file_id>" (input_file_id optional)
        """
        if not payload:
            return
        try:
            endpoint, fileid = payload.split("||", 1)
        except ValueError:
            endpoint = payload
            fileid = ""
        endpoint = (endpoint or "").strip()
        fileid = (fileid or "").strip()

        if not endpoint:
            endpoint = "/v1/responses"

        # Build parameters to match OpenAI Batch API example
        params = {
            "endpoint": endpoint,
            # sensible default window; user can change in code if needed
            "completion_window": "24h",
        }
        if fileid:
            params["input_file_id"] = fileid

        self.notify(f"Creating batch (endpoint={endpoint})...", title="Create")
        try:
            resp = await self.client.batches.create(**params)
            # response shape may vary; try common attributes
            bid = getattr(resp, "id", None) or getattr(resp, "batch", None) or "<unknown>"
            self.notify(f"Batch created: {bid}", title="Create", timeout=4)
            # refresh list
            self.action_list_batches()
        except Exception as e:
            self.notify(f"Error creating batch: {e}", severity="error")
    
    async def open_create_modal_worker(self):
        """Fetch file list from API and open the CreateBatchModal with filenames mapped to ids."""
        # prefer cached files (from List Files) so the modal reflects the current UI list
        files = list(self.cached_files) if self.cached_files else []
        if not files:
            try:
                resp = await self.client.files.list()
                for f in resp.data:
                    display_name = f.filename or f.id
                    files.append((f.id, display_name))
            except Exception:
                files = []

        # push modal on main app; modal will return endpoint||fileid
        self.app.push_screen(CreateBatchModal(files=files), self.create_batch_worker)
        
class BatchManager(App):
    """
    Main application class, manages screen transitions.
    """
    CSS_PATH = "main.css"

    def on_mount(self) -> None:
        self.push_screen(KeySelectionScreen())

    def on_profile_selected(self, message: ProfileSelected) -> None:
        mgr = BatchManagerScreen(
            api_key=message.api_key,
            profile_name=message.profile_name
        )
        self.push_screen(mgr)

def copy_example_config_if_needed():
    target_config = get_config_path()
    config_dir = target_config.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if not target_config.exists():
        example_path = Path(__file__).parent / "config.ini.example"
        print(f"[INFO] Copying default config to {target_config}")
        shutil.copy(example_path, target_config)

def main():
    copy_example_config_if_needed()

    app = BatchManager()
    app.run()  

if __name__ == "__main__":
    main()