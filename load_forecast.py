from pybrain.datasets import SupervisedDataSet
from pybrain.tools.shortcuts import buildNetwork
from pybrain.supervised.trainers import BackpropTrainer


class Forecast:

    def __init__(self, *args, **kwargs):
        self.networks = {}
        self.predict_result = {}

    def bp_predict(self, dpid, history_speed, now_speed):
        # create a neutral network by Pybrain
        if len(history_speed) > 1:
            self.networks[dpid] = {}
            self.networks[dpid]["ds"] = SupervisedDataSet(4, 2)
            for speed in history_speed:
                self.networks[dpid]["ds"].addSample(tuple(speed[:4]), tuple(speed[:2]))
            self.networks[dpid]["net"] = buildNetwork(4, 2, 2, bias=True)
            self.networks[dpid]["trainer"] = BackpropTrainer(self.networks[dpid]["net"],
                                                             self.networks[dpid]["ds"])
            self.networks[dpid]["trainer"].trainUntilConvergence(maxEpochs=1000)
            self.predict_result[dpid] = self.networks[dpid]["net"].activate(now_speed[:4])
        else:
            return 0

    def get_result(self, src_dp):
        temp_speed = 999999999999999
        temp_dpid = 0
        for dpid, speed_array in self.predict_result.iteritems():
            speed_aver = (speed_array[0]+speed_array[1])/2
            if dpid != src_dp and speed_aver < temp_speed:
                temp_speed = speed_aver
                temp_dpid = dpid
        return temp_dpid
