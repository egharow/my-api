#!flask/bin/python
import json
from flask import Flask, Response
from helloworld.flaskrun import flaskrun
import requests

application = Flask(__name__)

@application.route('/', methods=['GET'])
def get():
    return Response(json.dumps({'Output': 'Hello World'}), mimetype='application/json', status=200)

@application.route('/', methods=['POST'])
def post():
    return Response(json.dumps({'Output': 'Hello World'}), mimetype='application/json', status=200)

@application.route('/check/<string:currency>', methods=['GET'])
def get_currency_check(currency):
    return Response(json.dumps({'result': currency_rate.get(currency) }), mimetype='application/json', status=200)

@application.route('/check/bitcoin/<currency>', methods=['GET'])
def get_bitcoin(currency):
    res = requests.get("https://api.coindesk.com/v1/bpi/currentprice.json").json();
    dic = res.get('bpi')
    value= dic.get(currency)
    #print(res.text)
    return Response(json.dumps(value), mimetype='application/json', status=200)

currency_rate = {
 'usd' : 3.3,
 'pound' : 4.5,
 'euro' : 4.8}




if __name__ == '__main__':
    flaskrun(application)
