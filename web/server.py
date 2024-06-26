import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import datetime, date
from flask import Flask, request, jsonify
from real_time_system.config import MONGODB_SERVER_ADDR, TOTAL_BUCKET, DB_NAME
from real_time_system.db_handler import connect_mongodb, scan_database, ConsistentHash, migrate_data
import pymysql.cursors

# Flask app
app = Flask(__name__)

# Connect to MongoDB
mongo_client = connect_mongodb(MONGODB_SERVER_ADDR)
if mongo_client == None:
    raise Exception(f"Cannot establish connection to MongoDB server at {MONGODB_SERVER_ADDR}")
num_db = scan_database(mongo_client)
if num_db == 0:
    raise Exception("No available database")
ch = ConsistentHash(num_bucket=TOTAL_BUCKET, num_db=num_db)

# Connect to MySQL database
mysql_conn = pymysql.connect(
    host='localhost',
    user='dsci551',
    password='',
    database=None,
    cursorclass=pymysql.cursors.DictCursor
)

# Cursor for executing queries
mysql_cursor = mysql_conn.cursor()

def get_realtime_stock_collection(symbol):
    db_id = ch.get_node(symbol)
    db_name = DB_NAME + db_id
    print(db_name)
    return mongo_client[db_name][symbol]

"""sample output:
[
  {
    "_id": "AAPL_2024-03-14 18:58:34.912639",
    "change": 1.87,
    "current_price": 173,
    "high_price": 174.3078,
    "low_price": 172.05,
    "open_price": 172.94,
    "percent_change": 1.0927,
    "prev_close_price": 171.13,
    "t": 1710446401,
    "timestamp": "Thu, 14 Mar 2024 18:58:34 GMT"
  }
]
"""
@app.route('/quote', methods=['GET'])
def quote():
    symbol = request.args.get("symbol")
    collection = get_realtime_stock_collection(symbol)
    latest_data = collection.find().sort('timestamp', -1).limit(1)
    return jsonify(list(latest_data))

"""sample output:
[
  {
    "current_price": 173,
    "timestamp": "Thu, 14 Mar 2024 18:58:34 GMT"
  },
  {
    "current_price": 173,
    "timestamp": "Thu, 14 Mar 2024 21:58:52 GMT"
  }
]
"""
@app.route('/quote_chart', methods=['GET'])
def quote_chart():
    symbol = request.args.get('symbol')
    start = request.args.get('start')
    end = request.args.get('end')

    format_start = datetime.combine(date.today(), datetime.strptime(start, '%H:%M:%S').time())
    format_end = datetime.combine(date.today(), datetime.strptime(end, '%H:%M:%S').time())

    query = {
        'timestamp': {
            '$gte': format_start,
            '$lte': format_end
        }
    }

    collection = get_realtime_stock_collection(symbol)
    data = collection.find(query, {'_id': 0, 'current_price': 1, 'timestamp': 1})
    
    return jsonify(list(data))

@app.route('/realtime/dbstats', methods=['GET'])
def dbstats():
    db_list = [name for name in mongo_client.list_database_names() if name.startswith(DB_NAME)]
    db_stat = {'stats':{}, 'collections':{}}
    for db_name in db_list:
        db = mongo_client[db_name]
        db_stat['stats'][db_name] = db.command('dbStats')
        db_stat['collections'][db_name] = {"collections": db.list_collection_names()}
    return jsonify(db_stat)

@app.route('/realtime/commands', methods=['GET'])
def commands():
    db = request.args.get("db")
    collection = request.args.get("collection")
    action = request.args.get("action")
    query = request.args.get("query")

    if action == "drop":
        try:
            result = mongo_client[db][collection].drop()
            print(result)
            return jsonify(message="Success!")
        except Exception as e:
            print(e)
            return jsonify(message="Fail!")
    if action == "count":
        res = mongo_client[db][collection].count_documents({})
        return jsonify(res)
    if action == "remove_expired":
        format_time = datetime.combine(date.today(), 
                                       datetime.strptime("00:00:00", '%H:%M:%S').time())
        res = mongo_client[db][collection].delete_many(
            {'timestamp': {'$lt': format_time}})
        return jsonify(res.deleted_count)
    if action == "drop_all":
        collections = mongo_client[db].list_collection_names()
        for c in collections:
            if c != "dummy":
                mongo_client[db][c].drop()
        return jsonify(message="Success!")
        
@app.route('/realtime/addnode', methods=['GET'])
def addnode():
    new_idx = ch.add_node(str(len(ch.nodes)))
    old_idx = (new_idx + 1) % len(ch.nodes)
    new_node = f"{DB_NAME}{ch.nodes[new_idx]}"
    old_node = f"{DB_NAME}{ch.nodes[old_idx]}"
    migrate_data(mongo_client=mongo_client, old_node=old_node, ch=ch)
    return jsonify({"new_db": new_node, "old_db": old_node})

@app.route('/realtime/deletenode', methods=['GET'])
def deletenode():
    node_id = len(ch.nodes) - 1
    if node_id < 0:
        return jsonify(message="No node to delete")
    node = f"{DB_NAME}{node_id}"
    ch.remove_node(str(node_id))
    migrate_data(mongo_client=mongo_client, old_node=node, ch=ch)
    return jsonify({"db": node})

#HISTORICAL
@app.route('/stock_list', methods=['GET'])
def get_stock_list():
    """
    Get stock list
    Arg: Symbol,company
    Return: json
    """
    query = f"SELECT symbol, company FROM STOCK.stocks"
    mysql_cursor.execute(query)
    data = mysql_cursor.fetchall()

    if data:
        return jsonify(data)
    else:
        return jsonify({"message": "No data found for the symbol"})
    
@app.route('/stock_chart_data', methods=['GET'])
def get_stock_chart_data():
    """
    Price in a time span from “start” to “end”
    Arg: Symbol,start,end
    Return: json
    """
    symbol = request.args.get('symbol')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = f"SELECT symbol, hdate, c FROM STOCK.stockhistory WHERE symbol = %s AND hdate BETWEEN %s AND %s"
    mysql_cursor.execute(query,(symbol, start_date, end_date))
    data = mysql_cursor.fetchall()

    if data:
        return jsonify(data)
    else:
        return jsonify({"message": "No data found for the symbol"})
    
@app.route('/stock_data', methods=['GET'])
def get_stock_data():
    """
    Price in a time span from “start” to “end” for TABLE
    Arg: Symbol,start,end
    Return: json
    """
    symbol = request.args.get('symbol')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = f"SELECT symbol, hdate, o, h, l, c FROM STOCK.stockhistory WHERE symbol = %s AND hdate BETWEEN %s AND %s"
    mysql_cursor.execute(query,(symbol, start_date, end_date))
    data = mysql_cursor.fetchall()

    if data:
        return jsonify(data)
    else:
        return jsonify({"message": "No data found for the symbol"})
    

@app.route('/db_stats', methods=['GET'])
def get_db_stats():
    """
    Database Baisc Stats
    Arg: table_name, table_schema, engine, 
         table_rows (# of rows in a table),
         update_time (last time table was updated)
    Return: json
    """
    #Get summary
    mysql_cursor.execute("SELECT table_name, table_schema, engine, table_rows, update_time from information_schema.tables")
    summary_stats=mysql_cursor.fetchall()
    
    return jsonify(summary_stats)

@app.route('/data_availability', methods=['GET'])
def get_data_availability():
    """
    Display data updates
    Arg: Date, Availability
    Return: json
    """
    mysql_cursor.execute("""SELECT sh.hdate AS Date, CONCAT(count(sh.symbol),'/', (SELECT count(*) FROM STOCK.stocks)) AS Availability 
                         FROM STOCK.stockhistory sh
                         GROUP BY sh.hdate""")
    data_availability=mysql_cursor.fetchall()
    
    return jsonify(data_availability)

#PORTFOLIO
@app.route('/user_verif', methods=['GET'])
def user_verification():
    """
    Verify user log in information
    Arg: username, password
    Return: True/False
    """
    usrname = request.args.get('username')
    passwrd = request.args.get('password')
    isadmin = request.args.get('isadmin') == "True"
    query = """
            SELECT 
                COUNT(*) > 0 AS is_exist,
                isAdmin,
                id
            FROM 
                SIMULATOR.users
            WHERE 
                username = %s AND 
                passcode = %s
            """
    mysql_cursor.execute(query, (usrname, passwrd))
    result = mysql_cursor.fetchone()
    
    if result:
        is_exist = result['is_exist']
        is_admin = result['isAdmin']
        user_id = result['id']
        if is_exist and is_admin == isadmin:
            return jsonify({'is_verified': True, 'user_id': user_id}), 200
    
    return jsonify({'is_verified': False, 'user_id': None}), 400

@app.route('/user_registration', methods=['POST'])
def user_registration():
    """
    User registration, insert data if user doesn't exist
    Arg: username
    Return: True/False
    """
    usrname = request.json.get('username')
    passwrd = request.json.get('password')
    isadmin = request.json.get('isadmin')
    query = """
            INSERT INTO SIMULATOR.users (username,passcode,isAdmin)
            VALUES (%s, %s, %s)
            """
    try:
        mysql_cursor.execute(query, (usrname, passwrd, isadmin))
        mysql_conn.commit()
        #Get the ID of the last inserted row
        user_id = mysql_cursor.lastrowid
        return jsonify({'is_registered': True, 'user_id': user_id}), 200  #If successfully inserted
    except pymysql.err.IntegrityError as err:
        if err.args[0] == 1062:  #Duplicate entry error
            return jsonify({'is_registered': False, 'user_id': None}), 400
        else:
            return jsonify({'error': str(err), 'user_id': None})

@app.route('/transaction', methods=['POST'])
def get_transaction():
    """
    Handles Buy/Sell on Webpage.
    If user purchase or sell stocks, database will be automatically updated with the changes
    Return: message
    """
    #NEED TO ADJUST CODE IN WEBPAGE TO ENSURE THAT WHEN USER PRESS "BUY" THE VOLUME IS POSITIVE, 
    #AND WHEN "SELL", THE VOLUME SHOULD BE THE INPUTET VOLUME VALUE*(-1)
    symbol = request.json.get('symbol')
    volume = request.json.get('volume')
    user_id = request.json.get('userId') #I DONT KNOW HOW TO GET THIS!!!

    if not (symbol and volume and user_id):
        return jsonify({'message': 'Invalid request parameters.'}), 400
    
    #Retrieve the user ID corresponding to the provided username
    """
    mysql_cursor.execute("SELECT id FROM users WHERE username = %s", (current_username,))
    user_id = mysql_cursor.fetchone()
    if not user_id:
        return jsonify({'message': f"User with username {current_username} not found."}), 404
    user_id = user_id[0]
    """
    
    # Insert transaction into transactions table
    if volume != 0:
        mysql_cursor.execute("INSERT INTO SIMULATOR.transactions (buyerId, symbol, volume) VALUES (%s, %s, %s)", 
                                (user_id, symbol, volume))
    else:
        return jsonify({'message': 'Volume must be nonzero.'}), 400
    mysql_conn.commit()

    # Update inventory table
    if volume >0:
        mysql_cursor.execute("""INSERT INTO SIMULATOR.inventory (userId, symbol, volume) VALUES (%s, %s, %s) \
                                    ON DUPLICATE KEY UPDATE volume = volume + %s""", (user_id, symbol, volume, volume))
    else:  #Sell
        mysql_cursor.execute("""INSERT INTO SIMULATOR.inventory (userId, symbol, volume) VALUES (%s, %s, %s) \
                                    ON DUPLICATE KEY UPDATE volume = volume - %s""", (user_id, symbol, volume, -volume))
    
    mysql_conn.commit()

    return jsonify({'message': 'Transaction processed successful.'}),200

# @app.route('/user_info', methods=['GET'])
# def get_user_info():
#     """
#     Display user info in the Portfolio Page
#     Arg: ID, username
#     Return: json
#     """
#     usrname = request.args.get('username')
#     query = "SELECT ID, username FROM users WHERE username = %s"
#     mysql_cursor.execute(query, (usrname, ))
#     user_info = mysql_cursor.fetchone()
    
#     return jsonify(user_info)

@app.route('/user_inventory', methods=['GET'])
def get_user_inventory():
    """
    Display current user's list of inventories
    Arg: symbol, volume
    Return: json
    """
    user_id = request.args.get('userId')
    query = """SELECT symbol, volume 
                FROM SIMULATOR.inventory
                WHERE userId = %s"""
    mysql_cursor.execute(query, (user_id))
    user_inv = mysql_cursor.fetchall()
    
    return jsonify(user_inv)

@app.route('/user_list', methods=['GET'])
def get_user_list():
    """
    Display user list in the Admin Page
    Return: json
    """
    query = "SELECT * FROM SIMULATOR.users"
    mysql_cursor.execute(query)
    user_list = mysql_cursor.fetchall()
    
    return jsonify(user_list)

@app.route('/insert_user', methods=['POST'])
def insert_user():
    """
    Insert a new user into the users table
    Args: username, password
    Returns: Success or failure message
    """
    usrname = request.json.get('username')
    passwrd = request.json.get('password')
    
    # Check if username already exists
    query_check = "SELECT username FROM SIMULATOR.users WHERE username = %s"
    mysql_cursor.execute(query_check, (usrname,))
    existing_username = mysql_cursor.fetchone()
    
    if existing_username:
        return jsonify({'message': 'Username already exists. Please try a different username.'}), 400
    
    # Insert the user if username is unique
    query_insert = "INSERT INTO SIMULATOR.users (username, passcode) VALUES (%s, %s)"
    mysql_cursor.execute(query_insert, (usrname, passwrd))
    mysql_conn.commit()

    return jsonify({'message': 'User inserted successfully'}), 200


@app.route('/delete_user', methods=['POST'])
def delete_user():
    """
    Delete a user from the users table
    Args: username
    Returns: Success or failure message
    """
    usrname = request.json.get('username')
    query = "DELETE FROM SIMULATOR.users WHERE username = %s AND isAdmin = False"
    mysql_cursor.execute(query, (usrname))
    if mysql_cursor.rowcount > 0:
        return jsonify({'message': 'User deleted successfully'}), 200
    else:
        return jsonify({'message': 'User not found or deletion failed'}), 400

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)