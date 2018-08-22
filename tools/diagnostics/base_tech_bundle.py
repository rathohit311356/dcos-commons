from service_bundle import ServiceBundle


class BaseTechBundle(ServiceBundle):
    def task_exec(self):
        raise NotImplementedError

    def create(self):
        raise NotImplementedError
