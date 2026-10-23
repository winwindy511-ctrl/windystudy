import os

for p in ['D:/windystudy/_check_clean.py', 'D:/windystudy/_step1_check.py', 'D:/windystudy/_check_clean.py']:
    if os.path.exists(p):
        os.remove(p)
        print('REMOVED', p)
print('DONE_CLEANUP')
