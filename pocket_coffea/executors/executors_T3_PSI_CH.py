import os
import sys
import socket
from coffea import processor as coffea_processor
from pocket_coffea.utils.network import get_proxy_path
from pocket_coffea.utils.network import check_port
from pocket_coffea.parameters.dask_env import setup_dask


class BaseExecutorFactory():

    def __init__(self, run_options, **kwargs):
        self.run_options = run_options
        self.setup()

    def get(self):
        return None

    def setup_proxyfile(self):
        if self.run_options.get('voms-proxy', None) is not None:
             self.x509_path = self.run_options['voms-proxy']
        else:
             _x509_localpath = get_proxy_path()
             # Copy the proxy to the home from the /tmp to be used by workers
             self.x509_path = os.environ['HOME'] + f'/{_x509_localpath.split("/")[-1]}'
             os.system(f'cp {_x509_localpath} {self.x509_path}')
             
    def setup(self):
        self.setup_proxyfile()
        # Set envs for lxplus
        self.set_env()
 
    def set_env(self):
        # define some environmental variable
        # that are general enought to be always useful
        vars= {
            "XRD_RUNFORKHANDLER": "1",
            "MALLOC_TRIM_THRESHOLD_" : "0",
            "X509_USER_PROXY": self.x509_path
        }
        for k,v in vars.items():
            os.environ[k] = v

    def customize_args(self, args):
        return args

    def close(self):
        pass

    
class IterativeExecutorFactory(BaseExecutorFactory):

    def __init__(self, run_options, **kwargs):
        super().__init__(run_options) 

    def get(self):
        return coffea_processor.iterative_executor


class FuturesExecutorFactory(BaseExecutorFactory):

    def __init__(self, run_options, **kwargs):
        super().__init__(run_options)

    def get(self):
        return coffea_processor.futures_executor

    def customize_args(self, args):
        args = super().customize_args(args)
        # in the futures executor Nworkers == N scalout
        args["workers"] = self.run_options["scaleout"]
        return args

    

class DaskExecutorFactory(BaseExecutorFactory):
    '''
    At T3_PSI_CH the dask executor is based on slurm
    '''

    def __init__(self, run_options, outputdir, **kwargs):
        self.outputdir = outputdir
        super().__init__(run_options)

    def get_worker_env(self):
        env_worker = [
            'export XRD_RUNFORKHANDLER=1',
            'export MALLOC_TRIM_THRESHOLD_=0',
            f'export X509_USER_PROXY={self.x509_path}',
            'ulimit -u unlimited',
            ]
        
        # Adding list of custom setup commands from user defined run options
        if "custom-setup-commands" in self.run_options:
            env_worker += self.run_options["custom-setup-commands"]

        # Now checking for conda environment  conda-env:true
        if "conda-env" in self.run_options:
            env_worker += [f'export PATH={os.environ["CONDA_PREFIX"]}/bin:$PATH',
                           f'conda activate {os.environ["CONDA_DEFAULT_ENV"]}']

        # if local-virtual-env: true the dask job is configured to pickup
        # the local virtual environment. 
        if "local-virtualenv" in self.run_options:
            env_worker.append(f"source {sys.prefix}/bin/activate")

        return env_worker
    
        
    def setup(self):
        ''' Start the DASK cluster here'''
        self.setup_proxyfile()
        # Setup dask general options from parameters/dask_env.py
        import dask.config
        from distributed import Client
        from dask_jobqueue import SLURMCluster
        setup_dask(dask.config)

        # Slurm cluster
        self.dask_cluster = SLURMCluster(
                queue=self.run_options['queue'],
                cores=self.run_options['cores-per-worker'],
                processes=self.run_options['cores-per-worker'],
                memory=self.run_options['mem-per-worker'],
                walltime=self.run_options["walltime"],
                job_script_prologue=self.get_worker_env(),
                local_directory=os.path.join(self.outputdir, "slurm_localdir"),
                log_directory=os.path.join(self.outputdir, "slurm_log"),
            )

        #Cluster adaptive number of jobs only if requested
        print(">> Sending out jobs")
        self.dask_cluster.adapt(minimum=1 if self.run_options["adaptive"]
                                else self.run_options['scaleout'],
                      maximum=self.run_options['scaleout'])
        
        self.dask_client = Client(self.dask_cluster)
        print(">> Waiting for the first job to start...")
        self.dask_client.wait_for_workers(1)
        print(">> You can connect to the Dask viewer at http://localhost:8787")

        # if self.run_options["performance-report"]:
        #     self.performance_report_path = os.path.join(self.outputdir, f"{log_folder}/dask-report.html")
        #     print(f"Saving performance report to {self.performance_report_path}")
        #     self.performance_report(filename=performance_report_path):

        
    def get(self):
        return coffea_processor.dask_executor 

    def customize_args(self, args):
        args = super().customize_args(args)
        # in the futures executor Nworkers == N scalout
        args["client"] = self.dask_client
        args["treereduction"] = self.run_options["tree-reduction"]
        args["retries"] = self.run_options["retries"]
        return args

    def close(self):
        self.dask_client.close()
        self.dask_cluster.close()




def get_executor_factory(executor_name, **kwargs):
    if executor_name == "iterative":
        return IterativeExecutorFactory(**kwargs)
    elif executor_name == "futures":
        return FuturesExecutorFactory(**kwargs)
    elif  executor_name == "dask":
        return DaskExecutorFactory(**kwargs)
