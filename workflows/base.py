import json
import coffea
from coffea import hist, processor, lookup_tools
from coffea.lumi_tools import LumiMask #, LumiData
from coffea.analysis_tools import PackedSelection, Weights
import os
import numpy as np
import awkward as ak

from lib.objects import lepton_selection, jet_selection, get_dilepton
from lib.cuts import dilepton
from parameters.preselection import object_preselection
from parameters.triggers import triggers
from parameters.btag import btag
from parameters.lumi import lumi
from parameters.samples import samples_info
from parameters.allhistograms import histogram_settings

from utils.config_loader import load_config

class ttHbbBaseProcessor(processor.ProcessorABC):
    def __init__(self, cfg='test.py'):
        #self.sample = sample
        # Read required cuts and histograms from config file
        self.cfg = load_config(cfg).cfg
        self._cuts_functions = self.cfg['cuts']
        if type(self._cuts_functions) is not list: raise NotImplementedError
        self._varnames = self.cfg['variables']
        self._hist2dnames = self.cfg['variables2d']
        # Save trigger and preselection requirements
        self._preselections = object_preselection['dilepton']
        # Save histogram settings of the required histograms
        self._variables = {var_name : histogram_settings['variables'][var_name] for var_name in self._varnames}
        self._variables2d = {hist2d_name : histogram_settings['variables2d'][hist2d_name] for hist2d_name in self._hist2dnames}
        self._hist_dict = {}
        self._hist2d_dict = {}

        # Define PackedSelector to save per-event cuts and dictionary of selections
        self._cuts = PackedSelection()
        self._selections = {}

        # Define axes
        dataset_axis = hist.Cat("dataset", "Dataset")
        cut_axis     = hist.Cat("cut", "Cut")
        year_axis    = hist.Cat("year", "Year")

        self._sumw_dict = {
            "sumw": processor.defaultdict_accumulator(float),
            "nevts": processor.defaultdict_accumulator(int),
        }

        #for var in self._vars_to_plot.keys():
        #       self._accumulator.add(processor.dict_accumulator({var : processor.column_accumulator(np.array([]))}))

        for var_name in self._varnames:
            if var_name.startswith('n'):
                field = var_name
            else:
                obj, field = var_name.split('_')
            variable_axis = hist.Bin( field, self._variables[var_name]['xlabel'], **self._variables[var_name]['binning'] )
            self._hist_dict[f'hist_{var_name}'] = hist.Hist("$N_{events}$", dataset_axis, cut_axis, year_axis, variable_axis)
        for hist2d_name in self._hist2dnames:
            varname_x = list(self._variables2d[hist2d_name].keys())[0]
            varname_y = list(self._variables2d[hist2d_name].keys())[1]
            variable_x_axis = hist.Bin("x", self._variables2d[hist2d_name][varname_x]['xlabel'], **self._variables2d[hist2d_name][varname_x]['binning'] )
            variable_y_axis = hist.Bin("y", self._variables2d[hist2d_name][varname_y]['ylabel'], **self._variables2d[hist2d_name][varname_y]['binning'] )
            self._hist2d_dict[f'hist2d_{hist2d_name}'] = hist.Hist("$N_{events}$", dataset_axis, cut_axis, year_axis, variable_x_axis, variable_y_axis)

        self._hist_dict.update(**self._hist2d_dict)
        self._hist_dict.update(**self._sumw_dict)
        self._accumulator = processor.dict_accumulator(self._hist_dict)        
        self.nobj_hists = [histname for histname in self._hist_dict.keys() if histname.lstrip('hist_').startswith('n') and not 'nevts' in histname]
        self.muon_hists = [histname for histname in self._hist_dict.keys() if 'muon' in histname and not histname in self.nobj_hists]
        self.electron_hists = [histname for histname in self._hist_dict.keys() if 'electron' in histname and not histname in self.nobj_hists]
        self.jet_hists = [histname for histname in self._hist_dict.keys() if 'jet' in histname and not 'fatjet' in histname and not histname in self.nobj_hists]

    @property
    def accumulator(self):
        return self._accumulator

    # Function to load year-dependent parameters
    def load_parameters(self):
        self._dataset = self.events.metadata["dataset"]
        self._sample = self.events.metadata["sample"]
        self._year = self.events.metadata["year"]
        self._triggers = triggers['dilepton'][self._year]
        self._btag = btag[self._year]

    # Function to compute masks to preselect objects and save them as attributes of `events`
    def apply_object_preselection(self):
        # Build masks for selection of muons, electrons, jets, fatjets
        self.good_muons, self.veto_muons = lepton_selection(self.events.Muon, self._preselections["muons"], self._year)
        self.good_electrons, self.veto_electrons = lepton_selection(self.events.Electron, self._preselections["electrons"], self._year)
        self.good_jets = jet_selection(self.events.Jet, self.events.Muon, (self.good_muons|self.veto_muons), self._preselections["jets"]) & jet_selection(self.events.Jet, self.events.Electron, (self.good_electrons|self.veto_electrons), self._preselections["jets"])
        self.good_bjets = self.good_jets & (getattr(self.events.Jet, self._btag["btagging_algorithm"]) > self._btag["btagging_WP"])
        self.good_fatjets = jet_selection(self.events.FatJet, self.events.Muon, self.good_muons, self._preselections["fatjets"]) & jet_selection(self.events.FatJet, self.events.Electron, self.good_electrons, self._preselections["fatjets"])

        # As a reference, additional masks used in the boosted analysis
        #self.good_jets_nohiggs = ( self.good_jets & (jets.delta_r(leading_fatjets) > 1.2) )
        #self.good_bjets_boosted = self.good_jets_nohiggs & (getattr(self.events.jets, self.parameters["btagging_algorithm"]) > self.parameters["btagging_WP"])
        #self.good_nonbjets_boosted = self.good_jets_nohiggs & (getattr(self.events.jets, self.parameters["btagging_algorithm"]) < self.parameters["btagging_WP"])

        self.events["MuonAll"]      = self.events.Muon[self.good_muons | self.veto_muons]
        self.events["ElectronAll"]  = self.events.Electron[self.good_electrons | self.veto_electrons]
        self.events["MuonGood"]     = self.events.Muon[self.good_muons]
        self.events["ElectronGood"] = self.events.Electron[self.good_electrons]
        self.events["JetGood"]      = self.events.Jet[self.good_jets]
        self.events["BJetGood"]     = self.events.Jet[self.good_bjets]
        self.events["FatJetGood"]   = self.events.FatJet[self.good_fatjets]
        self.events["ll"]           = get_dilepton(self.events.ElectronAll, self.events.MuonAll)

    # Function that counts the preselected objects and save the counts as attributes of `events`
    def count_objects(self):
        self.events["nmuongood"]     = ak.num(self.events.MuonGood)
        self.events["nelectrongood"] = ak.num(self.events.ElectronGood)
        self.events["nmuon"]         = ak.num(self.events.MuonAll)
        self.events["nelectron"]     = ak.num(self.events.ElectronAll)
        self.events["nlepgood"]      = self.events["nmuongood"] + self.events["nelectrongood"]
        self.events["nlep"]          = self.events["nmuon"] + self.events["nelectron"]
        self.events["njet"]          = ak.num(self.events.JetGood)
        self.events["nbjet"]         = ak.num(self.events.BJetGood)
        self.events["nfatjet"]       = ak.num(self.events.FatJetGood)

    # Function that computes the trigger masks and save the logical OR of the mumu, emu and ee triggers in the PackedSelector
    def apply_triggers(self):
        # Trigger logic
        self.trigger_mumu = np.zeros(len(self.events), dtype='bool')
        self.trigger_emu = np.zeros(len(self.events), dtype='bool')
        self.trigger_ee = np.zeros(len(self.events), dtype='bool')

        for trigger in self._triggers["mumu"]: self.trigger_mumu = self.trigger_mumu | self.events.HLT[trigger.lstrip("HLT_")]
        for trigger in self._triggers["emu"]:  self.trigger_emu  = self.trigger_emu  | self.events.HLT[trigger.lstrip("HLT_")]
        for trigger in self._triggers["ee"]:   self.trigger_ee   = self.trigger_ee   | self.events.HLT[trigger.lstrip("HLT_")]

        self._cuts.add('trigger', ak.to_numpy(self.trigger_mumu | self.trigger_emu | self.trigger_ee))
        self._selections['trigger'] = {'clean', 'trigger'}

    def apply_cuts(self):
        for cut_function in self._cuts_functions:
            mask = cut_function(self)
            self._cuts.add(cut_function.__name__, ak.to_numpy(mask))
            self._selections[cut_function.__name__] = set.union(self._selections['trigger'], {cut_function.__name__})

    def fill_histograms(self, output):
        for histname, h in output.items():
            if type(h) is not hist.Hist: continue
            for cut in self._selections.keys():
                if histname in self.nobj_hists:
                    weight = self.weights.weight() * self._cuts.all(*self._selections[cut])
                    fields = {k: ak.fill_none(getattr(self.events, k), -9999) for k in h.fields if k in histname}
                elif histname in self.muon_hists:
                    muon = self.events.MuonAll
                    weight = ak.flatten(self.weights.weight() * ak.Array(ak.ones_like(muon.pt) * self._cuts.all(*self._selections[cut])))
                    fields = {k: ak.flatten(ak.fill_none(muon[k], -9999)) for k in h.fields if k in dir(muon)}
                elif histname in self.electron_hists:
                    electron = self.events.ElectronAll
                    weight = ak.flatten(self.weights.weight() * ak.Array(ak.ones_like(electron.pt) * self._cuts.all(*self._selections[cut])))
                    fields = {k: ak.flatten(ak.fill_none(electron[k], -9999)) for k in h.fields if k in dir(electron)}
                elif histname in self.jet_hists:
                    jet = self.events.JetGood
                    weight = ak.flatten(self.weights.weight() * ak.Array(ak.ones_like(jet.pt) * self._cuts.all(*self._selections[cut])))
                    fields = {k: ak.flatten(ak.fill_none(jet[k], -9999)) for k in h.fields if k in dir(jet)}
                else:
                    #fields, weight = self.fill_histograms_extra(histname, h, cut, self.events)
                    continue
                h.fill(dataset=self.events.metadata["dataset"], cut=cut, year=self._year, **fields, weight=weight)
        output = self.fill_histograms_extra(output)

    def count_objects_extra(self):
        pass

    def fill_histograms_extra(self, output):
        pass

    def process_extra(self) -> ak.Array:
        pass

    def process(self, events):
        output = self.accumulator.identity()
        #if len(events)==0: return output
        self.events = events
        self.load_parameters()
        nEvents = ak.count(self.events.event)
        output['nevts'][self._sample] += nEvents
        self.isMC = 'genWeight' in self.events.fields
        if self.isMC:
            output['sumw'][self._sample] += sum(self.events.genWeight)

        if self._year =='2017':
            metstruct = 'METFixEE2017'
            MET = self.events.METFixEE2017
        else:
            metstruct = 'MET'
            MET = self.events.MET

        mask_clean = np.ones(nEvents, dtype=np.bool)

        # Event cleaning and  PV selection
        flags = [
            "goodVertices", "globalSuperTightHalo2016Filter", "HBHENoiseFilter", "HBHENoiseIsoFilter", "EcalDeadCellTriggerPrimitiveFilter", "BadPFMuonFilter"]#, "BadChargedCandidateFilter", "ecalBadCalibFilter"]
        if not self.isMC:
            flags.append("eeBadScFilter")
        for flag in flags:
            mask_clean = mask_clean & getattr(self.events.Flag, flag)
        mask_clean = mask_clean & (self.events.PV.npvsGood > 0)

        # Weights
        self.weights = Weights(nEvents)
        if self.isMC:
            self.weights.add('genWeight', self.events.genWeight)
            self.weights.add('lumi', ak.full_like(self.events.genWeight, lumi[self._year]))
            self.weights.add('XS', ak.full_like(self.events.genWeight, samples_info[self._sample]["XS"]))
            self.weights.add('sumw', ak.full_like(self.events.genWeight, 1./output["sumw"][self._sample]))

        # In case of data: check if event is in golden lumi file
        if not self.isMC and not (lumimask is None):
            mask_lumi = lumimask(self.events.run, self.events.luminosityBlock)
            mask_clean = mask_clean & mask_lumi

        self._cuts.add('clean', ak.to_numpy(mask_clean))

        leadFatJet = ak.firsts(self.events.FatJet)

        self.apply_object_preselection()
        self.count_objects()
        self.apply_triggers()
        self.apply_cuts()
        
        # This function is empty in the base processor, but can be overriden in processors derived from the class ttHbbBaseProcessor
        self.process_extra()

        self.fill_histograms(output)

        return output

    def postprocess(self, accumulator):

        return accumulator
