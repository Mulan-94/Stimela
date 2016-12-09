## Dockerized reduction srcipt framework for radio astronomy
# Sphesihle Makhathini <sphemakh@gmail.com>

import os
import sys
import stimela
from stimela import docker
import stimela.utils as utils
import stimela.cargo as cargo
import tempfile
import time
import inspect
import platform
from stimela.utils import stimela_logger

USER = os.environ["USER"]
UID = os.getuid()


ekhaya = cargo.__path__[0]

CONFIGS_ = {
    "cab/simms" : "{:s}/configs/simms_params.json".format(ekhaya),
    "cab/h5toms" : "{:s}/configs/h5toms_params.json".format(ekhaya),
    "cab/simulator" : "{:s}/configs/simulator_params.json".format(ekhaya),
    "cab/lwimager" : "{:s}/configs/imager_params.json".format(ekhaya),
    "cab/wsclean" : "{:s}/configs/imager_params.json".format(ekhaya),
    "cab/casa" : "{:s}/configs/imager_params.json".format(ekhaya),
    "cab/predict" : "{:s}/configs/simulator_params.json".format(ekhaya),
    "cab/calibrator" : "{:s}/configs/calibrator_params.json".format(ekhaya),
    "cab/sourcery" : "{:s}/configs/sourcery_params.json".format(ekhaya),
    "cab/flagms" : "{:s}/configs/flagms_params.json".format(ekhaya),
    "cab/autoflagger" : "{:s}/configs/autoflagger_params.json".format(ekhaya),
    "cab/subtract" : "{:s}/configs/subtract_params.json".format(ekhaya)
}

class PipelineException(Exception):
    """
    Encapsulates information about state of pipeline when an
    exception occurs
    """
    def __init__(self, exception, completed, failed, remaining):
        message = ("Exception occurred while running "
            "pipeline component '%s': %s" % (failed.label, str(exception)))

        super(PipelineException, self).__init__(message)

        self._completed = completed
        self._failed = failed
        self._remaining = remaining

    @property
    def completed(self):
        return self._completed

    @property
    def failed(self):
        return self._failed

    @property
    def remaining(self):
        return self._remaining

class Recipe(object):

    def __init__(self, name, data=None, configs=None,
                 ms_dir=None, cab_tag=None,
                 container_logfile=None, shared_memory=1024):

        # LOG recipe
        procs = stimela_logger.Process(stimela.LOG_PROCESS)

        date = "{:d}/{:d}/{:d}-{:d}:{:d}:{:d}".format(*time.localtime()[:6])
        procs.add( dict(name=name.replace(" ", "_"), date=date, pid=os.getpid()) )
        procs.write()

        self.stimela_context = inspect.currentframe().f_back.f_globals

        self.name = name
        self.log = utils.logger(0,
                   logfile="log-%s.txt"%name.replace(" ","_").lower())

        self.containers = []
        self.active = None
        self.configs_path = configs
        self.data_path = data or self.stimela_context.get("STIMELA_DATA", None)
        if self.data_path:
            pass
        else:
            raise TypeError("'data' option has to be specified")

        self.configs_path_container = "/configs"
        self.stimela_path = os.path.dirname(docker.__file__)
        self.CAB_TAG = cab_tag

        self.ms_dir = ms_dir or self.stimela_context.get("STIMELA_MSDIR", None)
        if self.ms_dir:
            if not os.path.exists(self.ms_dir):
                os.mkdir(self.ms_dir)

        home = os.environ["HOME"] + "/.stimela/stimela_containers.log"
        self.CONTAINER_LOGFILE = container_logfile or home


        self.shared_memory = shared_memory


    def add(self, image, name, config,
            input=None, output=None, label="",
            build_first=False, build_dest=None,
            saveconf=None, add_time_stamp=True,
            shared_memory="1gb", tag=None):


        if image =="cab/casa":
            image = "cab/casa_clean"

        input = input or self.stimela_context.get("STIMELA_INPUT", None)
        output = output or self.stimela_context.get("STIMELA_OUTPUT", None)

        cab_tag = self.CAB_TAG or self.stimela_context.get("CAB_TAG", None)
        cab_tag = tag if tag!=None else cab_tag


        if build_first and build_dest:
            self.build(image, build_dest)

        if add_time_stamp:
            name = "%s-%s"%(name, str(time.time()).replace(".", ""))

        # Add tag if its specified
        if cab_tag:
            image = image.split(":")[0]
            image = "{:s}:{:s}".format(image, cab_tag)


        cont = docker.Container(image, name,
                label=label, logger=self.log,
                shared_memory=shared_memory)

        # add standard volumes
        cont.add_volume(self.stimela_path, "/utils", perm="ro")
        cont.add_volume(self.data_path, "/data", perm="ro")

        if self.ms_dir:
            md = "/home/%s/msdir"%USER
            cont.add_volume(self.ms_dir, md)
            cont.add_environ("MSDIR", md)

        if input:
            cont.add_volume( input,"/input")
            cont.add_environ("INPUT", "/input")

        if output:
            if not os.path.exists(output):
                os.mkdir(output)

            od = "/home/%s/output"%USER
            cont.add_volume(output, od)
            cont.add_environ("OUTPUT", od)


        # Check if imager image was selected. React accordingly
        if image == "cab/imager":
            if isinstance(config, dict):
                imager = config.get("imager", None)
            else:
                config_ = self.readJson(config)
                imager = config_.get("imager", None)

            imager = imager or "lwimager"

            image = "cab/" + imager
            cont.image = image


        if isinstance(config, dict):
            if not os.path.exists("configs"):
                os.mkdir("configs")

            if not saveconf:
                saveconf = "configs/%s-%s.json"%(self.name.replace(" ", "_").lower(), name)

            confname_container = "%s/%s"%(self.configs_path_container,
                        os.path.basename(saveconf))


            if image.split(":")[0] in CONFIGS_:
                template = utils.readJson(CONFIGS_[image.split(":")[0]])
                template.update(config)
                config = template
            utils.writeJson(saveconf, config)

            config = confname_container
            cont.add_volume("configs", self.configs_path_container, perm="ro")
        else:
            cont.add_volume(self.configs_path, self.configs_path_container, perm="ro")
            config = self.configs_path_container+"/"+config

        cont.image = "{:s}_{:s}".format(USER, image)
        cont.add_environ("CONFIG", config)

        self.containers.append(cont)

        # Record base image info
        dockerfile = cargo.CAB_PATH +"/"+ image.split("/")[-1]
        base_image = utils.get_Dockerfile_base_image(dockerfile)
        self.log.info("<=BASE_IMAGE=> {:s}={:s}".format(image, base_image))


    def run(self, steps=None, log=True):
        """
            Run pipeline
        """

        if isinstance(steps, (list, tuple, set)):
            if isinstance(steps[0], str):
                labels = [ cont.label.split("::")[0] for cont in self.containers]
                containers = []

                for step in steps:
                    try:
                        idx = labels.index(step)
                    except ValueError:
                        raise ValueError("Recipe label ID [{:s}] doesn't exist".format(step))

                    containers.append( self.containers[idx] )
            else:
                containers = [ self.containers[i-1] for i in steps[:len(self.containers)] ]
        else:
            containers = self.containers

        for i, container in enumerate(containers):
            try:
                self.log.info("Running Container %s"%container.name)
                self.log.info("STEP %d :: %s"%(i, container.label))
                self.active = container

                container.create()
                container.start()
            except Exception as e:
                completed = containers[:i]
                remaining = containers[i:]

                pe = PipelineException(e, completed, container, remaining)

                raise pe, None, sys.exc_info()[2]
            finally:
                container.stop()
                container.remove()


        self.log.info("Pipeline [%s] ran successfully. Will now attempt to clean up dead containers "%(self.name))

        self.log.info("\n[================================DONE==========================]\n \n")

        # Remove from log
        procs = stimela_logger.Process(stimela.LOG_PROCESS)
        procs.rm(os.getpid())
        procs.write()


    def build(self, name, dest, use_cache=True):
        try:
            utils.xrun("docker", ["build", "-t", name,
                       "--no-cache=%s"%("false" if use_cache else "true"),
                       dest] )
        except SystemError:
            raise docker.DockerError("Docker image failed to build")


    def stop(self, log=True):
        """
            Stop all running containers
        """
        for container in self.containers:
            container.stop(logfile=self.CONTAINER_LOGFILE)


    def rm(self, containers=None, log=True):
        """
            Remove all stopped containers
        """
        for container in containers or self.containers:
            container.rm(logfile=self.CONTAINER_LOGFILE)


    def clear(self):
        """
            Clear container list.
            This does nothing to the container instances themselves
        """
        self.containers = []


    def pause(self):
        """
            Pause current container. This effectively pauses the pipeline
        """
        if self.active:
            self.active.pause()


    def resume(self):
        """
            Resume puased container. This effectively resumes the pipeline
        """
        if self.active:
            self.active.resume()


    def readJson(self, config):
        return utils.readJson(self.configs_path+"/"+config)
