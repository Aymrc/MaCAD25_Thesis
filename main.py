# This is the main function of the copilot
# It starts the copilot back and front-end

from llm import llm

def copilot_start():
    copilot_name = "To_be_Determined"
    print("Starting LLM...")
    llm.run_llm()
    print("Copilot {} started...".format(copilot_name))


if __name__ == "__main__":
    copilot_start()