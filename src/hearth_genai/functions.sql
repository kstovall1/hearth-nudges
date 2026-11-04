-- The two governed functions behind nudge copy. The daily job, the judge eval and the app all call them,
-- so every message, generated or edited by a marketer, gets the same prompt rules and the same check.
-- Inputs are facts about the nudge only: no names, contact details or sensitive attributes reach either model.
USE CATALOG IDENTIFIER(:catalog);

-- The facts a message may use, as one line of plain text
CREATE OR REPLACE FUNCTION hearth_ml.nudge_brief(nudge_type STRING, metro STRING, days_away_bucket STRING, new_member BOOLEAN)
RETURNS STRING
COMMENT 'The facts a nudge message may use. Shared by write_nudge_copy and check_nudge_copy.'
RETURN CASE nudge_type
  WHEN 'new_likes' THEN 'The member has new likes waiting from people they have not seen yet.'
  WHEN 'match_waiting' THEN 'A match sent the member a message that they have not replied to yet.'
  WHEN 'profile_tip' THEN 'The member joined recently. Suggest one quick profile improvement, such as adding a photo or answering a prompt, so people can get to know them.'
  WHEN 'new_people_nearby' THEN concat('New people joined Hearth near the member recently, in ',
    CASE WHEN metro IS NULL OR metro = 'Other' THEN 'their area' ELSE metro END, '.')
  WHEN 'we_miss_you' THEN concat('The member has not opened the app in ', replace(days_away_bucket, ' days', ''),
    ' days. Invite them back warmly.')
END;

-- Writes one push notification with Claude Haiku. NULL if the model call fails.
CREATE OR REPLACE FUNCTION hearth_ml.write_nudge_copy(brief STRING)
RETURNS STRING
COMMENT 'Writes one push notification for a nudge brief with system.ai.claude-haiku-5-5, asking for 90 characters or fewer. Returns NULL if the call fails. check_nudge_copy enforces the rules.'
RETURN trim(ai_query(
  'system.ai.claude-haiku-5-5',
  concat(
    'You write push notifications for Hearth, a dating app. Write one notification of at most 90 characters.\n',
    'Voice: warm, friendly, light and plain.\n',
    'Rules:\n',
    '- Use only the facts below. Do not invent numbers, names, matches or outcomes.\n',
    '- Do not promise a match, a date or a result.\n',
    '- Do not guilt the member about being away, and no false urgency.\n',
    '- Do not mention age, gender, sexual orientation, appearance, or who they are looking for.\n',
    '- No names, emojis, hashtags, links, numbers or quotation marks. No time-of-day greetings.\n',
    'Reply with only the final notification, on one line, with no explanation or notes.\n\n',
    'Facts: ', brief),
  -- Haiku 5.5 reasons before it writes, and those tokens count toward max_tokens. At 300 about half the calls
  -- came back empty with no error. 1000 with low effort returned a message every time in testing. It rejects
  -- temperature and structured output.
  modelParameters => named_struct('max_tokens', 1000, 'reasoning_effort', 'low'),
  failOnError => false
).result);

-- Checks one message against its brief with OpenJev through ai_decide, plus rules that need no model.
-- passed is true only if every check clears. reasons lists the checks that failed.
CREATE OR REPLACE FUNCTION hearth_ml.check_nudge_copy(brief STRING, copy STRING)
RETURNS STRUCT<passed BOOLEAN, reasons ARRAY<STRING>, personal_info DOUBLE, sensitive_trait DOUBLE,
               unsupported_claim DOUBLE, guilt_or_pressure DOUBLE, on_topic DOUBLE, tone DOUBLE>
COMMENT 'Checks a nudge message for PII, sensitive traits, unsupported claims, guilt or pressure, topic and tone with ai_decide (OpenJev), plus length and character rules. Thresholds: each risk below 0.5, on_topic at least 0.5, tone at least 1 on a 0 to 2 scale.'
RETURN WITH d AS (
  SELECT ai_decide(
    to_json(named_struct('brief', brief, 'message', copy)),
    '{
      "personal_info": {"type": "noul",
        "instructions": "Does the message include a person''s name, an email address, a phone number, a street address, a link or any other personal identifier?"},
      "sensitive_trait": {"type": "noul",
        "instructions": "Does the message mention or imply the member''s age, gender, sexual orientation, who they are looking for, body or appearance, religion, ethnicity or health?"},
      "unsupported_claim": {"type": "noul",
        "instructions": "Does the message state or promise something the brief does not support, such as a number of likes, a guaranteed match or date, a named person, or a fact not in the brief?"},
      "guilt_or_pressure": {"type": "noul",
        "instructions": "Does the message guilt-trip or shame the member for being away, or use false urgency, threats or fear of missing out?"},
      "on_topic": {"type": "noul",
        "instructions": "Is the message about the nudge described in the brief?"},
      "tone": {"type": "score",
        "instructions": "How well does the message fit a warm, friendly, light dating app voice?",
        "criteria": ["Off-brand: cold, pushy, robotic, crude or awkward",
                     "Acceptable but flat or generic",
                     "On-brand: warm, friendly, light and clear"]}
    }',
    map('version', '1.0')):response:answers AS a
),
s AS (
  SELECT
    a:personal_info:probability::DOUBLE AS personal_info,
    a:sensitive_trait:probability::DOUBLE AS sensitive_trait,
    a:unsupported_claim:probability::DOUBLE AS unsupported_claim,
    a:guilt_or_pressure:probability::DOUBLE AS guilt_or_pressure,
    a:on_topic:probability::DOUBLE AS on_topic,
    a:tone:score::DOUBLE AS tone
  FROM d
),
r AS (
  SELECT *, filter(array(
    CASE WHEN copy IS NULL OR length(trim(copy)) < 10 THEN 'empty' END,
    CASE WHEN length(copy) > 90 THEN 'too_long' END,
    CASE WHEN copy RLIKE '[\\r\\n]' THEN 'not_one_line' END,
    CASE WHEN copy RLIKE '[0-9@#]|https?:|www[.]' THEN 'digits_or_links' END,
    CASE WHEN personal_info IS NULL THEN 'check_failed' END,
    CASE WHEN personal_info >= 0.5 THEN 'personal_info' END,
    CASE WHEN sensitive_trait >= 0.5 THEN 'sensitive_trait' END,
    CASE WHEN unsupported_claim >= 0.5 THEN 'unsupported_claim' END,
    CASE WHEN guilt_or_pressure >= 0.5 THEN 'guilt_or_pressure' END,
    CASE WHEN on_topic < 0.5 THEN 'off_topic' END,
    CASE WHEN tone < 1.0 THEN 'off_brand' END
  ), x -> x IS NOT NULL) AS reasons
  FROM s
)
SELECT named_struct('passed', size(reasons) = 0, 'reasons', reasons, 'personal_info', personal_info,
  'sensitive_trait', sensitive_trait, 'unsupported_claim', unsupported_claim, 'guilt_or_pressure', guilt_or_pressure,
  'on_topic', on_topic, 'tone', tone)
FROM r;
