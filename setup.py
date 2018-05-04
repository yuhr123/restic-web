from setuptools import setup

setup(
    name='restic-web',
    packages=['restic_web'],
    version=1.0,
    author='Herald Yu',
    author_email='yuhr123@gmail.com',
    include_package_data=True,
    install_requires=[
        'flask',
        'flask-restful',
        'flask-sqlalchemy',
        'flask-bcrypt',
    ]
)