"""Every number the simulation plants, in one place. docs/data-design.md explains them."""

N_MEMBERS_START = 67_000  # members who signed up before the window
SIGNUPS_PER_DAY = 135  # about 8K more during a 60-day window
N_DAYS = 60
INCIDENT_DAY = 34  # 0-based, so the blast triples from day 35

# Members
AGE_BANDS = ["18-24", "25-29", "30-34", "35-44", "45+"]
AGE_WEIGHTS = [0.31, 0.29, 0.19, 0.14, 0.07]
GENDERS = ["woman", "man", "nonbinary"]
GENDER_WEIGHTS = [0.47, 0.49, 0.04]
SEEKING = ["men", "women", "everyone"]
SEEKING_WEIGHTS = {  # by gender identity
    "woman": [0.80, 0.10, 0.10],
    "man": [0.08, 0.88, 0.04],
    "nonbinary": [0.15, 0.15, 0.70],
}
# metro, market size, timezone, weight
METROS = [
    ("New York", "large", "America/New_York", 0.17),
    ("Los Angeles", "large", "America/Los_Angeles", 0.13),
    ("Chicago", "large", "America/Chicago", 0.09),
    ("Dallas", "large", "America/Chicago", 0.07),
    ("Atlanta", "medium", "America/New_York", 0.06),
    ("Phoenix", "medium", "America/Phoenix", 0.05),
    ("Seattle", "medium", "America/Los_Angeles", 0.05),
    ("Denver", "medium", "America/Denver", 0.04),
    ("Miami", "medium", "America/New_York", 0.04),
    ("Boise", "small", "America/Boise", 0.02),
    ("Spokane", "small", "America/Los_Angeles", 0.015),
    ("Des Moines", "small", "America/Chicago", 0.015),
    ("Other", "small", None, 0.25),  # timezone drawn from the four US zones
]
OTHER_TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"]
OTHER_TZ_WEIGHTS = [0.45, 0.30, 0.10, 0.15]
PLATFORMS = ["ios", "android"]
PLATFORM_WEIGHTS = [0.58, 0.42]
PLANS = ["free", "plus", "premium"]
PLAN_WEIGHTS = [0.82, 0.13, 0.05]
PRICE_USD = {"plus": 29.99, "premium": 49.99}
PUSH_OPT_IN = 0.71
PUSHES_PER_WEEK_BEFORE = 1.3  # so fatigue doesn't start from zero on day 0

# Activity
SESSION_RATE_MEDIAN = 0.22  # sessions per day for an engaged member
SESSION_RATE_SIGMA = 0.75  # lognormal spread, so a few members drive most activity
SESSION_RATE_FLOOR = 0.12  # quieter than this counts as lapsing, not engaged
SESSION_MEDIAN_SECONDS = 360
PROFILE_VIEWS_PER_MINUTE = 0.45
PAID_ACTIVITY_MULT = 1.4
WEEKEND_MULT = 1.2
INITIAL_STATE_WEIGHTS = {"engaged": 0.58, "decay": 0.07, "dormant": 0.35}
LAPSE_HAZARD = 0.006  # engaged to decaying, per day
LAPSE_HAZARD_NEW = 0.02  # in the first 14 days after signup
PAID_LAPSE_MULT = 0.6
DECAY_DAYS = (7, 14)  # how long the decline lasts before going quiet
DECAY_HALF_LIFE = 7  # sessions halve each week
REPLY_HALF_LIFE = 4  # replies drop faster than sessions
# Daily chance a dormant member comes back on their own, by days since last session
DORMANT_RETURN = [(2, 0.060), (7, 0.028), (15, 0.010)]

# Preferred hour, local time: mostly evenings, some lunch, some mornings
PREF_HOUR_GROUPS = [((19, 23), 0.62), ((12, 14), 0.23), ((7, 9), 0.15)]
SESSION_NEAR_PREF = 0.7  # share of sessions within a couple of hours of it

# Notifications
BLAST_UTC_HOUR = 13  # 9:00 Eastern, 6:00 Pacific during daylight time
DECISION_UTC_HOUR = 7  # the messaging tool's daily batch
TEST_HOLDOUT = 0.15
TEST_SEND_HOURS = (8, 22)  # local, inclusive
TEST_COOLDOWN_DAYS = 3
ELIGIBLE_DAYS_AWAY = 2
DELIVERY_RATE = 0.97
OPEN_RATE_ACTIVE = 0.10  # blast opens from members already active
OPEN_RATE_RETURNER = 0.35  # share of natural returns in a nudge window that come through the push
FATIGUE_OPEN_DECAY = 0.85  # open rate multiplier for each extra push in 7 days
HOUR_FACTOR_NEAR = 1.0  # within 2 hours of the preferred hour
HOUR_FACTOR_DAY = 0.6
HOUR_FACTOR_NIGHT = 0.3  # 23:00 to 07:00 local
OPT_OUT_BASE = 0.004  # per delivered push
OPT_OUT_FATIGUE = [(5, 3.5), (3, 1.8)]  # pushes in the last 7 days, multiplier
OPT_OUT_NIGHT_MULT = 1.5
UNINSTALL_SHARE = 0.2  # of opt-outs, the rest turn off push

# Uplift in points of 72-hour return at the member's preferred hour.
# Other hours scale it by the hour factor above.
EFFECT_MATCH_WAITING = 0.10
EFFECT_NEW_LIKES = {"18-24": 0.07, "25-29": 0.07, "30-34": 0.04, "35-44": 0.01, "45+": 0.01}
EFFECT_PROFILE_TIP_NEW = 0.05  # first 30 days
EFFECT_PROFILE_TIP_OTHER = 0.0
PROFILE_TIP_OTHER_OPT_OUT = 1.3
EFFECT_NEARBY = {"large": 0.04, "medium": 0.015, "small": -0.01}
NEARBY_SMALL_OPT_OUT = 2.0
# we_miss_you by days away: (min days, effect, opt-out multiplier)
EFFECT_WE_MISS_YOU = [(2, 0.01, 1.0), (3, 0.03, 1.0), (8, 0.015, 1.5), (15, 0.0, 3.0)]

# Paying
TRIAL_HAZARD = 0.0008  # per day a free member is active
TRIAL_DAYS = 7
TRIAL_CONVERT = 0.35
MONTHLY_CANCEL = 0.06
UPGRADE_RETURN_MATCH = 0.025  # free returners with a match in 72 hours, within 14 days
UPGRADE_RETURN_NO_MATCH = 0.002
UPGRADE_ACTIVE_DAILY = 0.0001
UPGRADE_PLUS_SHARE = 0.75

# Raw file quirks, as shares of events
BAD_NULL_MEMBER = 0.003
BAD_DUPLICATE = 0.01
BAD_FUTURE_TS = 0.001
BAD_UNKNOWN_TYPE = 0.002
UNKNOWN_EVENT_TYPES = ["screen_view", "ad_impression", "SESSION_START"]
APP_VERSION_FROM_DAY = 39  # 0-based, so app_version appears on day 40

NUDGES = ["new_likes", "match_waiting", "profile_tip", "new_people_nearby", "we_miss_you"]
COPY = {
    "new_likes": [
        "You have {n} new likes waiting on Hearth",
        "Someone likes you. See who on Hearth",
    ],
    "match_waiting": [
        "Your match is waiting on your reply",
        "A match messaged you. Don't leave them hanging",
    ],
    "profile_tip": [
        "Add a photo to get more matches",
        "Profiles with prompts get more likes. Add one in a minute",
    ],
    "new_people_nearby": [
        "New people joined near you this week",
        "Fresh faces in {metro}. Take a look",
    ],
    "we_miss_you": [
        "We miss you! Come see who's new on Hearth",
        "It's been a while. Your next match could be waiting",
    ],
}
