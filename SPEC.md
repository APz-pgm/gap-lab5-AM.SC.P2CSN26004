# What it does

This program takes one non-empty topic and asks the configured language model to generate exactly five study questions. It extracts JSON from the reply and validates it against the question schema. It records each model call, its outcome, latency, token counts, and estimated cost in an append-only JSON Lines log. It retries invalid output once with a repair instruction and retries call errors with a bounded backoff. The final status line tells the caller whether the paper was accepted.

# Inputs

- Command-line input: exactly one topic string. Leading and trailing whitespace is removed; a missing or whitespace-only topic is rejected before any model call.
- Model configuration: `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` are read through the supplied, unmodified `client.py`. The key is never logged or printed.
- Prices used for `openai/gpt-oss-120b` on Groq: $0.15 USD per million input tokens and $0.60 USD per million output tokens, from Groq's official model pricing page (checked 2026-10-03). The pricing page is https://console.groq.com/docs/model/openai/gpt-oss-120b.
- Generation settings: `temperature=0.2` and `max_tokens=1800`, supplied on every model request.
- Per-call logging destination: `RUN_LOG_PATH` if set; otherwise `run_log.jsonl` in the current working directory. Calls append one JSON object per line.
- Token usage: use positive provider-reported prompt and completion counts when both are present. If usage is absent or either count is zero, estimate prompt and completion tokens separately as approximately one token per four characters of the sent messages and returned text, with a minimum of one for non-empty text. Mark the record `tokens_estimated=true` whenever estimates are used.

# Outputs

The last non-empty standard-output line is exactly one of `ok`, `invalid_output`, `refused`, or `error`. A successful validated response prints `ok` and exits 0. Invalid output, refusal, or exhausted model-call errors print the matching status and exit 1. Invalid command-line input prints `error` and exits 2 before any call. Any progress text, if added, must appear before this final status line.

`run_log.jsonl` is appended, never rewritten, with exactly one record per actual model call, including every retry. All records from one program run share one `run_id`. Every record contains these nine required keys: `timestamp` (UTC ISO 8601 string), `run_id` (string), `model` (string), `status` (`ok`, `invalid_output`, `refused`, or `error`), `prompt_tokens` (integer or null), `completion_tokens` (integer or null), `latency_ms` (integer), `cost_usd` (number or null), and `tokens_estimated` (boolean). `cost_usd` is calculated from the configured per-million-token prices. A call that raises before returning a response has unknown token counts and cost, recorded as null; zero is reserved for a measured zero. Logs contain no API key, full prompt, or full reply.

The accepted model response is a JSON object containing exactly one key, `questions`, whose value contains exactly five question objects. Each question has exactly `question`, `options`, `answer`, and `explanation`. Question and explanation are non-blank strings of 10–300 characters. Options contains exactly four non-blank strings of 1–200 characters. Answer is non-blank and exactly equals one of the options. Valid plain, fenced, or short-preamble JSON is accepted after extraction.

# Failure cases

- Missing, extra, or empty command-line topic: make no model call, write no log record, print `error`, exit 2.
- Valid model response: log `ok`; do not retry; print `ok`, exit 0.
- Empty, malformed, or schema-invalid response: log `invalid_output`; make exactly one repair retry using an instruction to return corrected schema-conforming JSON. If the retry is still invalid, log it and print `invalid_output`, exit 1. Thus an invalid response results in exactly two call records.
- Explicit model refusal: log `refused`; never retry; print `refused`, exit 1.
- Model-call exception: log `error` with null token counts and null cost. Retry up to three total attempts, using delay `(2 ** attempt) + random.uniform(0, 1)` seconds before each retry, capped at 8 seconds. If a provider supplies `Retry-After` longer than the calculated delay, honor that longer delay. After attempts are exhausted, print `error`, exit 1. Each attempt has its own log record.
- Disable any SDK-internal retry behavior so the application's capped retry loop owns retries and every actual network attempt gets its own record.
- Provider omits usage or reports a zero token count: estimate both counts with the four-characters-per-token method, set `tokens_estimated=true`, and calculate estimated cost from those counts.
- Call or validation runtime failure other than an ordinary invalid response: report `error` and exit 1; do not accept the response.

# Acceptance checks

- Run `python main.py "photosynthesis"` with real provider configuration: last status `ok`, exit 0, and one log record if no retry is needed.
- Run `python main.py ""`: status `error`, exit 2, no model call and no new log record.
- For each stub mode, check final status, exit code, per-run log-record count, required keys, and one shared `run_id`; do not assert exact generated text. `stub:ok`, `stub:fenced`, and `stub:preamble` each finish `ok`/0 with one record. `stub:malformed`, `stub:badshape`, and `stub:empty` finish `invalid_output`/1 with two records. `stub:refused` finishes `refused`/1 with one record. `stub:error` finishes `error`/1 with two or three error records. `stub:flaky` finishes `ok`/0 with two records, first `error`, then `ok`. `stub:ratelimit` finishes `error`/1 with two or three error records and honors the stub's one-second `Retry-After` when it exceeds the calculated delay.
- Verify records are parseable JSON objects, have the nine required keys and correct types, match the printed outcome, and contain no secret or full prompt/reply. Confirm `run_log.jsonl` and `.env` are ignored by Git and absent from Git history.
- Run `python check.py .` after implementation.

# Out of scope

The program does not provide a graphical interface, persistent study-session storage, user accounts, authentication, or conversation history. It does not modify `client.py`, make model calls through another library, retry refusals, or retry more than the specified caps. It does not expose or store API keys, full prompts, or full replies in logs or standard output.
