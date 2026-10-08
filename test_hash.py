
import bcrypt
db_hash = b'.tUtV5.Em'
pwd = b'Password123!'
try:
    print(bcrypt.checkpw(pwd, db_hash))
except Exception as e:
    print('Error:', e)

