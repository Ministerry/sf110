import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# Let's remove the weight variables cleanly
patterns = [
    r'\s*"mod_new":.*?,\n',
    r'\s*"mod_accessible":.*?,\n',
    r'\s*"mod_field":.*?,\n',
    r'\s*"mod_none":.*?,\n',
    r'\s*"impact_nonmodified_multiplier":.*?,\n',
    r'\s*"bonus_return_no_side_effects":.*?,\n',
    r'\s*"penalty_focal_return_unmodified":.*?,\n',
]
for p in patterns:
    text = re.sub(p, '\n', text)

# Write back
with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'w') as f:
    f.write(text)
