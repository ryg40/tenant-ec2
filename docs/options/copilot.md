# Copilot model routes

[Options](README.md) | [Core install](../install.md) | [Compose stack](../reference/compose-stack.md)

The core uses Bedrock through the instance role.
The Copilot option adds an account login, the `copilot` service and routes `copilot/luna` and `copilot/embed`.
It does not remove `bedrock/luna` or `bedrock/embed`.

Set `EC2_OPTIONS=copilot`, or add `copilot` to a list that already contains `pangolin`.
The bootstrap adds `compose.copilot.yaml` to the selected edge file set.
The service uses the LiteLLM kit image from the lock and the models in `litellm/copilot.yaml`.

## Select the models

| Setting | Core value | Copilot choice |
| --- | --- | --- |
| `EC2_PI_DURABLE_MODEL` | `bedrock/luna` | `copilot/luna` |
| `EC2_OPENVIKING_VLM_MODEL` | `bedrock/luna` | `copilot/luna` |
| `EC2_OPENVIKING_EMBED_MODEL` | `bedrock/embed` | `copilot/embed` |
| `EC2_OPENVIKING_EMBED_DIMENSION` | `1024` | `1536` for the pinned `copilot/embed` model. |
| `EC2_PI_DURABLE_THINKING` | `xhigh` | Stays `xhigh`. |

The gateway must not lower Luna's reasoning effort or set `drop_params`.
A virtual key allows only the selected active model routes that its service needs.

Warning: changing an embedding model or vector size can make existing memory vectors incompatible. Plan a rebuild before changing populated memory.

## Select before a deploy

1. Set `EC2_OPTIONS` in `.env` to include `copilot`. The scripts select the service without a manual Compose edit.
2. Fill `EC2_COPILOT_MASTER_KEY` with a new value. The option requires its own credential, not an existing install's credential.
3. Choose the model routes in `.env`. Leave the core values unchanged if you only want extra gateway routes.
4. Follow the [core install](../install.md) from the secret step. The active option block now enters the host secret.

## First login

The Copilot device login needs a browser once.
The token files stay on the data volume, so a replacement instance can reuse them.
With operations enabled, volume snapshots also contain those tokens.

Warning: deleting a token file does not remove its snapshot copies. Set a backup retention policy and revoke exposed tokens.
Complete these actions from a shell on the host after the LiteLLM kit clone exists.

1. Enter the pinned LiteLLM kit directory. It contains the login command of the locked kit.

```sh
cd /opt/tenant-ec2/kits/litellm
```

2. Run the login with the image name from `kits.lock`. The command saves the token files to the Copilot data directory.

```sh
sudo COPILOT_TOKEN_DIR=/srv/data/copilot \
    LITELLM_IMAGE=<litellm-image-from-kits.lock> \
    sh scripts/login-copilot.sh login
```

3. Complete the device login in your browser. Use the account authorized for this install.
4. Enter the install kit directory. Its environment file selects the active overlays.

```sh
cd /opt/tenant-ec2
```

5. Recreate the Copilot service. It reads the saved token files.

```sh
sudo docker compose up -d --force-recreate copilot
```

6. Repeat the [host verify](../reference/deploy-script.md#verify-and-dry-run). Investigate a gateway or model failure before agent use.

Not verified: a live Copilot login or model call on this host layout.
No core install step requires the Copilot account or these token files.
