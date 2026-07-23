from openai import OpenAI

client = OpenAI()

history = []

while True:
    prompt = input("gpt> ")

    if prompt in ["exit", "quit"]:
        break

    history.append({
        "role": "user",
        "content": prompt
    })

    response = client.responses.create(
        model="gpt-5",
        input=history
    )

    answer = response.output_text

    print(f"\nGPT> {answer}\n")

    history.append({
        "role": "assistant",
        "content": answer
    })
