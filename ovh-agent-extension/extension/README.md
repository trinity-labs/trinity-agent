# 🟪 TRINITY Agent

An independent, open-source coding assistant for **OVHcloud AI Endpoints**, with conversation archives, tool approvals, task resumption, steering and a persistent follow-up queue.

**This extension is the client for the self-hosted TRINITY Agent backend. Installing this VSIX alone does not install Python, the agent backend or an OVH account.** Use it in the supplied Linux Docker environment, or connect VS Code Remote to that environment. It is not an official OVHcloud or OpenAI extension.

## ⬛ Install and configure

1. Deploy the companion backend using the [repository installation guide](https://github.com/trinity-labs/trinity-agent/blob/main/README.rst) and its Docker Compose example. The backend and Python dependencies must be installed on the same host as the VS Code extension.
2. Install this extension in that environment. With VS Code Remote, install it on the remote host.
3. If you use a different installation path, set `ovhAgent.backendPath` in **User settings** to the absolute path of `ovh-agent.sh`. The launcher must implement the bundled JSON-lines protocol and load the backend dependencies and secrets.
4. Configure your OVHcloud AI key through the extension command. The supplied Docker deployment persists it in its configured secret directory; never commit it to Git.
5. Open a trusted workspace and start a conversation. The backend can read and modify files and run commands with the permissions of its container. Review the access mode and container mounts.

The extension itself is free and licensed under **0BSD**. OVHcloud API usage and any hosting are charged by their providers. No model subscription or API credit is included.

## ⬛ Features

- Model selection and context compaction with local Markdown memory.
- Persistent conversation history, deletion and interrupted-task resumption.
- **Queue message** while a task runs; edit, remove or promote pending messages to steering.
- **Steer task** applies the instruction before the next model call, after the current call/tool completes. It does not roll back running commands.
- Pending text messages survive restarts and require explicit queue continuation after a stop/restart. Images are supported in ordinary prompts, not in queued messages.
- French, English, German and Spanish interface; automatic locale or Docker override.
- Configurable names, colors and custom logos in self-hosted deployments.

## ⬛ Privacy and storage

Prompts, relevant workspace contents, tool results and images can be sent to the configured AI endpoint. Conversations, task state and the follow-up queue are stored on the backend's persistent volume. Deleting a local conversation does not delete provider-side logs. The extension does not include a separate analytics service.

## ⬛ Source, issues and licensing

[GitHub](https://github.com/trinity-labs/trinity-agent) · [Codeberg](https://codeberg.org/trinity-labs/trinity-agent) · [Issues](https://github.com/trinity-labs/trinity-agent/issues)

Original code: 0BSD. Bootstrap Icons: MIT. The Marketplace icon is the TRINITY logo. The history header uses Font Awesome Free's **hexagon-nodes** icon under CC BY 4.0. DOMPurify, Markdown-it and other dependencies retain their respective licenses; license files are included in the package.
