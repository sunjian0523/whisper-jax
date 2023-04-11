import base64
from functools import partial
from multiprocessing import Pool

import gradio as gr
import numpy as np
import requests
from processing_whisper import WhisperPrePostProcessor
from transformers.models.whisper.tokenization_whisper import TO_LANGUAGE_CODE
from transformers.pipelines.audio_utils import ffmpeg_read


title = "Whisper JAX: The Fastest Whisper API ⚡️"

description = "Whisper JAX is an optimised implementation of the [Whisper model](https://huggingface.co/openai/whisper-large-v2) by OpenAI. It runs on JAX with a TPU v4-8 in the backend. Compared to PyTorch on an A100 GPU, it is over **12x** faster, making it the fastest Whisper API available."
# description += "\nYou can submit requests to Whisper JAX through this Gradio Demo, or directly through API calls (see below). This notebook demonstrates how you can run the Whisper JAX model yourself on a TPU v2-8 in a Google Colab: TODO."

API_URL = "https://whisper-jax.ngrok.io/generate/"

article = "Whisper large-v2 model by OpenAI. Backend running JAX on a TPU v4-8 through the generous support of the [TRC](https://sites.research.google/trc/about/) programme. Whisper JAX code and Gradio demo by 🤗 Hugging Face."

language_names = sorted(TO_LANGUAGE_CODE.keys())
CHUNK_LENGTH_S = 30
BATCH_SIZE = 16
NUM_PROC = 4


def query(payload):
    response = requests.post(API_URL, json=payload)
    return response.json(), response.status_code


def inference(inputs, language=None, task=None, return_timestamps=False):
    payload = {"inputs": inputs, "task": task, "return_timestamps": return_timestamps}

    # langauge can come as an empty string from the Gradio `None` default, so we handle it separately
    if language:
        payload["language"] = language

    data, status_code = query(payload)

    if status_code == 200:
        text = data["text"]
    else:
        text = data["detail"]

    if return_timestamps:
        timestamps = data["chunks"]
    else:
        timestamps = None

    return text, timestamps


def chunked_query(payload):
    response = requests.post("https://whisper-jax.ngrok.io/generate_from_features", json=payload)
    return response.json()


def forward(batch, task=None, return_timestamps=False):
    feature_shape = batch["input_features"].shape
    batch["input_features"] = base64.b64encode(batch["input_features"].tobytes()).decode()
    outputs = chunked_query(
        {"batch": batch, "task": task, "return_timestamps": return_timestamps, "feature_shape": feature_shape}
    )
    outputs["tokens"] = np.asarray(outputs["tokens"])
    return outputs


if __name__ == "__main__":
    processor = WhisperPrePostProcessor.from_pretrained("openai/whisper-large-v2")

    def transcribe_audio(microphone, file_upload, task, return_timestamps):
        warn_output = ""
        if (microphone is not None) and (file_upload is not None):
            warn_output = (
                "WARNING: You've uploaded an audio file and used the microphone. "
                "The recorded file from the microphone will be used and the uploaded audio will be discarded.\n"
            )

        elif (microphone is None) and (file_upload is None):
            return "ERROR: You have to either use the microphone or upload an audio file"

        inputs = microphone if microphone is not None else file_upload

        with open(inputs, "rb") as f:
            inputs = f.read()

        inputs = ffmpeg_read(inputs, processor.feature_extractor.sampling_rate)
        inputs = {
            "array": base64.b64encode(inputs.tobytes()).decode(),
            "sampling_rate": processor.feature_extractor.sampling_rate,
        }

        text, timestamps = inference(inputs=inputs, task=task, return_timestamps=return_timestamps)

        return warn_output + text, timestamps

    def transcribe_chunked_audio(microphone, file_upload, task, return_timestamps):
        warn_output = ""
        if (microphone is not None) and (file_upload is not None):
            warn_output = (
                "WARNING: You've uploaded an audio file and used the microphone. "
                "The recorded file from the microphone will be used and the uploaded audio will be discarded.\n"
            )

        elif (microphone is None) and (file_upload is None):
            return "ERROR: You have to either use the microphone or upload an audio file"

        inputs = microphone if microphone is not None else file_upload

        with open(inputs, "rb") as f:
            inputs = f.read()

        inputs = ffmpeg_read(inputs, processor.feature_extractor.sampling_rate)
        inputs = {"array": inputs, "sampling_rate": processor.feature_extractor.sampling_rate}

        dataloader = processor.preprocess_batch(inputs, chunk_length_s=CHUNK_LENGTH_S, batch_size=BATCH_SIZE)

        pool = Pool(NUM_PROC)

        model_outputs = pool.map(partial(forward, task=task, return_timestamps=return_timestamps), dataloader)

        post_processed = processor.postprocess(model_outputs, return_timestamps=return_timestamps)
        timestamps = post_processed.get("chunks")
        return warn_output + post_processed["text"], timestamps

    def _return_yt_html_embed(yt_url):
        video_id = yt_url.split("?v=")[-1]
        HTML_str = (
            f'<center> <iframe width="500" height="320" src="https://www.youtube.com/embed/{video_id}"> </iframe>'
            " </center>"
        )
        return HTML_str

    def transcribe_youtube(yt_url, task, return_timestamps):
        html_embed_str = _return_yt_html_embed(yt_url)

        text, timestamps = inference(inputs=yt_url, task=task, return_timestamps=return_timestamps)

        return html_embed_str, text, timestamps

    audio = gr.Interface(
        fn=transcribe_audio,
        inputs=[
            gr.inputs.Audio(source="microphone", optional=True, type="filepath"),
            gr.inputs.Audio(source="upload", optional=True, type="filepath"),
            gr.inputs.Radio(["transcribe", "translate"], label="Task", default="transcribe"),
            gr.inputs.Checkbox(default=False, label="Return timestamps"),
        ],
        outputs=[
            gr.outputs.Textbox(label="Transcription"),
            gr.outputs.Textbox(label="Timestamps"),
        ],
        allow_flagging="never",
        title=title,
        description=description,
        article=article,
    )

    audio_chunked = gr.Interface(
        fn=transcribe_chunked_audio,
        inputs=[
            gr.inputs.Audio(source="microphone", optional=True, type="filepath"),
            gr.inputs.Audio(source="upload", optional=True, type="filepath"),
            gr.inputs.Radio(["transcribe", "translate"], label="Task", default="transcribe"),
            gr.inputs.Checkbox(default=False, label="Return timestamps"),
        ],
        outputs=[
            gr.outputs.Textbox(label="Transcription"),
            gr.outputs.Textbox(label="Timestamps"),
        ],
        allow_flagging="never",
        title=title,
        description=description,
        article=article,
    )

    youtube = gr.Interface(
        fn=transcribe_youtube,
        inputs=[
            gr.inputs.Textbox(lines=1, placeholder="Paste the URL to a YouTube video here", label="YouTube URL"),
            gr.inputs.Radio(["transcribe", "translate"], label="Task", default="transcribe"),
            gr.inputs.Checkbox(default=False, label="Return timestamps"),
        ],
        outputs=[
            gr.outputs.HTML(label="Video"),
            gr.outputs.Textbox(label="Transcription"),
            gr.outputs.Textbox(label="Timestamps"),
        ],
        allow_flagging="never",
        title=title,
        examples=[["https://www.youtube.com/watch?v=m8u-18Q0s7I", "transcribe", False]],
        cache_examples=False,
        description=description,
        article=article,
    )

    demo = gr.Blocks()

    with demo:
        gr.TabbedInterface(
            [audio, audio_chunked, youtube], ["Transcribe Audio", "Transcribe Audio Chunked", "Transcribe YouTube"]
        )

    demo.queue()
    demo.launch()
