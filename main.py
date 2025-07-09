

def install_requirements_if_needed():
    req_file = "requirements.txt"
    if os.path.exists(req_file):
        print("Checking/installing requirements from requirements.txt...")
        try:
            subprocess.check_call([python_path, "-m", "pip", "install", "-r", req_file])

            print("All requirements installed.")
        except subprocess.CalledProcessError as e:
            print("Failed to install requirements: {}".format(e))
    else:
        print("requirements.txt not found!")

install_requirements_if_needed()