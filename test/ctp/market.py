from package.zmq import subscriber

sub=subscriber.ZmqSubscriber(address="tcp://127.0.0.1:20001", topic_filter="market")
data=sub.get_data()
print(data)
