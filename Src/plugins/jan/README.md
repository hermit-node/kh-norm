# Jan remote adapter

Norm Schema-2 adapter for a Jan OpenAI-compatible API reachable over Tailscale. Configure it in Installer > Environment > Remote agents. `jan_chat` resolves `primary`/`secondary` through Norm's shared model settings; credentials stay in Norm's secrets file (`NORM_JAN_API_KEY`). The adapter never discovers or probes arbitrary hosts.
