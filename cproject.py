from flask import Flask, render_template, request, redirect
from flask import jsonify, url_for, flash, make_response
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, Catalog, Item, User
from flask import session as login_session
import random
import string
import requests
from oauth2client.client import flow_from_clientsecrets, FlowExchangeError
import httplib2
import json

app = Flask(__name__)

engine = create_engine('sqlite:///itemcatalog.db')
Base.metadata.bind = engine
DBSession = sessionmaker(bind=engine)
session = DBSession()

# Load clint info from client_secrets.json file
CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']
APPLICATION_NAME = "Catalog Item"


@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in range(32))
    login_session['state'] = state
    return render_template('login.html', STATE=state)

# Add facebook login Oauth


@app.route('/fbconnect', methods=['POST'])
def fbconnect():
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = request.data
    print ("access token received %s ") % access_token

    app_id = json.loads(open('fb_client_secrets.json', 'r').read())[
        'web']['app_id']
    app_secret = json.loads(
        open('fb_client_secrets.json', 'r').read())['web']['app_secret']
    url = 'https://graph.facebook.com/oauth/access_token'\
        '?grant_type=fb_exchange_token&client_id=%s'\
        '&client_secret=%s&fb_exchange_token=%s' % (
            app_id, app_secret, access_token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]

    # Use token to get user info from API
    userinfo_url = "https://graph.facebook.com/v3.2/me"
    token = result.split(',')[0].split(':')[1].replace('"', '')

    url = 'https://graph.facebook.com/v3.2/me'\
        '?access_token=%s&fields=name,id,email' % token
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]

    data = json.loads(result)
    login_session['provider'] = 'facebook'
    login_session['username'] = data["name"]
    login_session['email'] = data["email"]
    login_session['facebook_id'] = data["id"]

    # The token must be stored in the login_session in order to properly logout
    login_session['access_token'] = token

    # Get user picture
    url = 'https://graph.facebook.com/v3.2/me/picture'\
        '?access_token=%s&redirect=0&height=200&width=200' % token
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]
    data = json.loads(result)

    login_session['picture'] = data["data"]["url"]

    # see if user exists
    user_id = getUserID(login_session['email'])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']

    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; '\
        'height: 300px;border-radius: 150px;'\
        '-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '

    flash("You now logged in as %s" % login_session['username'])
    return output

# Disconnect facebook account from the app


@app.route('/fbdisconnect')
def fbdisconnect():
    facebook_id = login_session.get('facebook_id')
    # The access token must me included to successfully logout
    access_token = login_session['access_token']
    url = 'https://graph.facebook.com/%s/permissions?access_token=%s' % (
        facebook_id, access_token)
    h = httplib2.Http()
    result = h.request(url, 'DELETE')[1]
    return "you have been logged out"

# Add Google login Oauth


@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s' %
           access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print ("Token's client ID does not match app's.")
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_access_token = login_session.get('access_token')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_access_token is not None and gplus_id == stored_gplus_id:
        response = make_response(
            json.dumps('Current user is already connected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    # ADD PROVIDER TO LOGIN SESSION
    login_session['provider'] = 'google'

    # see if user exists, if it doesn't make a new one
    user_id = getUserID(data["email"])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;'\
        'border-radius: 150px;-webkit-border-radius: 150px;'\
        '-moz-border-radius: 150px;"> '
    flash("you are now logged in as %s" % login_session['username'])
    print ("done!")
    return output

# User Helper Functions


def createUser(login_session):
    newUser = User(name=login_session['username'],
                   email=login_session['email'],
                   picture=login_session['picture'])
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def getUserInfo(user_id):
    user = session.query(User).filter_by(id=user_id).one()
    return user


def getUserID(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None

# Disconnect Google account from the app


@app.route('/gdisconnect')
def gdisconnect():
    # Only disconnect a connected user.
    access_token = login_session.get('access_token')
    if access_token is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]
    if result['status'] == '200':
        response = make_response(json.dumps('Successfully disconnected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response
    else:
        response = make_response(json.dumps(
            'Failed to revoke token for given user.'), 400)
        response.headers['Content-Type'] = 'application/json'
        return response

# Add function to return JSON format of the database


@app.route('/catalog.json/')
def catalogsJSON():
    catalogs = session.query(Catalog).all()
    return jsonify(catalogs=[i.serialize for i in catalogs])


@app.route('/catalog/<catalog_name>/json/')
def catalogItemsJSON(catalog_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    items = session.query(Item).filter_by(catalog_id=catalog.id).all()
    return jsonify(items=[i.serialize for i in items])


@app.route('/catalog/<catalog_name>/item/<item_name>/json')
def itemJSON(catalog_name, item_name):
    item = session.query(Item).filter_by(name=item_name).one()
    return jsonify(item=item.serialize)

# Main page of the app


@app.route('/')
@app.route('/catalog/')
def showMain():
    catalogs = session.query(Catalog).order_by(asc(Catalog.name)).all()
    if 'username' not in login_session:
        return render_template('publicMain.html', catalogs=catalogs)
    else:
        return render_template('catalogs.html', catalogs=catalogs)

# make new category


@app.route('/catalog/new/', methods=['GET', 'POST'])
def newCatalog():
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        newCatalog = Catalog(
            name=request.form['name'], user_id=login_session.get('user_id'))
        session.add(newCatalog)
        session.commit()
        flash("New catalog created!")
        return redirect(url_for('showCatalog', catalog_name=newCatalog.name))
    else:
        return render_template('newCatalog.html')

# edit category


@app.route('/catalog/<catalog_name>/edit/', methods=['GET', 'POST'])
def editCatalog(catalog_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    if 'username' not in login_session:
        return redirect('/login')
    if catalog.user_id != login_session.get('user_id'):
        return "<script>function myFunction()"\
            "{alert('You are not authorized to edit this catalog."\
            "Please create your own catalog in order to edit.');}"\
            "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        if request.form['name']:
            catalog.name = request.form['name']
        session.add(catalog)
        session.commit()
        flash("Catalog has been edited!")
        return redirect(url_for('showCatalog', catalog_name=catalog.name))
    else:
        return render_template('editCatalog.html', catalog=catalog)

# delete existing catalog


@app.route('/catalog/<catalog_name>/delete/', methods=['GET', 'POST'])
def deleteCatalog(catalog_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    if 'username' not in login_session:
        return redirect('/login')
    if catalog.user_id != login_session.get('user_id'):
        return "<script>function myFunction()"\
            "{alert('You are not authorized to delete this catalog."\
            "Please create your own catalog in order to delete.');}"\
            "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        session.delete(catalog)
        session.commit()
        flash("Catalog has been deleted")
        return redirect(url_for('showMain'))
    else:
        return render_template('deleteCatalog.html', catalog=catalog)

# display the items of the catalog


@app.route('/catalog/<catalog_name>/item/')
def showCatalog(catalog_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    items = session.query(Item).filter_by(catalog_id=catalog.id).all()
    if 'username' not in login_session:
        return render_template('publicCatalog.html',
                               catalog=catalog, items=items)
    else:
        return render_template('showCatalog.html',
                               catalog=catalog, items=items)

# display the details of the item


@app.route('/catalog/<catalog_name>/item/<item_name>/')
def showItem(catalog_name, item_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    item = session.query(Item).filter_by(name=item_name).one()
    if 'username' not in login_session:
        return render_template('publicItem.html', catalog=catalog, item=item)
    else:
        return render_template('showItem.html', catalog=catalog, item=item)

# create new item


@app.route('/catalog/<catalog_name>/item/new/', methods=['GET', 'POST'])
def newItem(catalog_name):
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    if 'username' not in login_session:
        return redirect('/login')
    if catalog.user_id != login_session.get('user_id'):
        return "<script>function myFunction()"\
            "{alert('You are not authorized to "\
            "add item to this catalog."\
            "Please create your own catalog in "\
            "order to add new items to it.');}"\
            "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        newItem = Item(name=request.form['name'],
                       description=request.form['description'],
                       catalog_id=catalog.id, user_id=catalog.user_id)
        session.add(newItem)
        session.commit()
        flash("new item successfully created")
        return redirect(url_for('showCatalog',
                                catalog_name=catalog_name))
    else:
        return render_template('newItem.html', catalog=catalog)

# edit existing item


@app.route('/catalog/<catalog_name>/item/<item_name>/edit/',
           methods=['GET', 'POST'])
def editItem(catalog_name, item_name):
    item = session.query(Item).filter_by(name=item_name).one()
    catalog = session.query(Catalog).filter_by(name=catalog_name).one()
    if 'username' not in login_session:
        return redirect('/login')
    if catalog.user_id != login_session.get('user_id'):
        return "<script>function myFunction()"\
            "{alert('You are not authorized to edit this item."\
            "Please create your own item in order to edit.');}"\
            "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        if request.form['name']:
            item.name = request.form['name']
        if request.form['description']:
            item.description = request.form['description']
        session.add(item)
        session.commit()
        flash("Item has been edited")
        return redirect(url_for('showItem',
                                catalog_name=catalog_name,
                                item_name=item.name))
    else:
        return render_template('editItem.html',
                               item=item,
                               catalog=catalog)

# delete existing item


@app.route('/catalog/<catalog_name>/item/<item_name>/delete/',
           methods=['GET', 'POST'])
def deleteItem(catalog_name, item_name):
    item = session.query(Item).filter_by(name=item_name).one()
    catalog = session.query(Catalog).filter_by(id=item.catalog_id).one()
    if 'username' not in login_session:
        return redirect('/login')
    if catalog.user_id != login_session.get('user_id'):
        return "<script>function myFunction()"\
            "{alert('You are not authorized to delete this item."\
            "Please create your own item in order to delete.');}"\
            "</script><body onload='myFunction()'>"
    if request.method == 'POST':
        session.delete(item)
        session.commit()
        flash("Item has been deleted")
        return redirect(url_for('showCatalog', catalog_name=catalog_name))
    else:
        return render_template('deleteItem.html', item=item, catalog=catalog)

# Disconnect based on provider


@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            del login_session['access_token']
        if login_session['provider'] == 'facebook':
            fbdisconnect()
            del login_session['facebook_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect(url_for('showMain'))
    else:
        flash("You were not logged in")
        return redirect(url_for('showMain'))


if __name__ == '__main__':
    app.secret_key = 'secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=8000)
