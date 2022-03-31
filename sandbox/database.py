from sandbox_queue import db


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer)
    tsp_id = db.Column(db.Integer)
    job_dir = db.Column(db.String)

    def __repr__(self):
        return f"<Job {self.job_id} {self.tsp_id} {self.job_dir}>"
