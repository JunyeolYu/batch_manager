# batch-manager

`batch-manager` is a CLI tool for batch job and file management using only the OpenAI API.

## Key Features

- Monitor batch job status and manage logs
- Manage storage (file upload and deletion)

## Installation

```bash
git clone https://github.com/JunyeolYu/batch_manager.git
cd batch_manager
pip install .
```

### Requirements

- Python >= 3.9
- textual >= 1.0.0
- openai

## Usage

```bash
python3 batch_manager/main.py

# or

batch_manager
```

## Configure API Keys

On the first run, a config file will be created at `~/.config/batch_manager/config.ini`.

The template looks like this:
```ini
[Test API KEY]
api_key = sk-proj-xxx... # <= replace this section with your own API key

[Test API KEY2]
api_key = sk-proj-xxx... # <= replace this section with your own API key
```

Each API can be assigned a unique key inside the brackets (`[]`). When using `batch_manager`, simply select the desired key to choose which API to use.

## TODO
- [ ] Implement `batch creation` as a modal window
- [ ] Integrate file upload into the `batch creation`
- [ ] Add a "Quit" button