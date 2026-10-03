# Cost

Across five successful real Groq question papers, the measured average was 1.00 model call per paper (5 calls / 5 papers). The estimated average cost was $0.00076149 USD per paper; at the same measured usage, 1,000 papers are estimated to cost $0.76149 USD. These estimates use Groq's listed `openai/gpt-oss-120b` rates: $0.15 per million input tokens and $0.60 per million output tokens ([official model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b)).

# Speed

For the five real calls, measured median latency was 3,263 ms and measured p95 latency was 4,095 ms (nearest-rank method). The slowest call was 4,095 ms; it was not a retry. Each of the five runs used one call.

# Method

The figures use five real Groq runs on five different topics. Prompt and completion tokens came from provider usage, and latency was measured around each call; no token estimates were needed. Costs are estimates calculated from those measured token counts and Groq's published rates. The 20-line sample log contains the five real records plus 15 offline stub records to meet the evidence sample size; the stub records are excluded from the reported metrics.
