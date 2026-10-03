# Decisions

## 2026-09-20 - Define a fixed five-question JSON schema

I decided that the application should generate exactly five study questions, with each question containing `question`, `options`, `answer`, and `explanation`.

I chose this instead of allowing a flexible number of questions or extra fields because a fixed schema makes the generated output easier to validate automatically and gives the application a clear acceptance condition.

## 2026-09-20 - Accept fenced and preamble JSON

I decided to accept valid JSON even when the model surrounds it with Markdown code fences or short preamble text.

I considered rejecting any response that was not pure JSON from the beginning, but rejected that approach because a model can produce usable JSON together with harmless formatting or introductory text. The application therefore extracts the JSON before applying the schema validation.

## 2026-09-20 - Detect refusals before JSON validation

I decided to check the model response for recognized refusal phrases before attempting JSON parsing.

I considered treating every non-JSON response as `invalid_output`, but rejected that approach because an explicit model refusal is a different failure condition. Separating `refused` from `invalid_output` makes the application's final status more informative and matches the required failure categories.

## 2026-10-03 - Keep repair retries separate from transient-error retries

I decided to allow exactly one repair request after invalid model output, while allowing up to three total attempts for call errors. I rejected using one shared retry count because malformed content needs a correction prompt, while a transient connection or rate-limit error needs a delayed retry of the same request. Separate caps also make the spend bound and each logged attempt clear.

## 2026-10-03 - Estimate both token counts when provider usage is incomplete

I decided to use the supplied four-characters-per-token estimate whenever usage is missing or either count is zero, and to mark both counts as estimated. I rejected mixing a reported count with an estimated count because the required log has one `tokens_estimated` flag for the pair, and a consistent method makes each cost easier to interpret.

## 2026-10-03 - Log only call metadata

I decided to write status, token counts, latency, model, and calculated cost, but not the prompt, reply, or exception message. I rejected logging request and response text for easier debugging because those fields can contain the user's topic or other sensitive content; the required metrics can be calculated without storing that text.
