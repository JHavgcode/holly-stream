import os
import cv2
import torch
import numpy
import typing
import imutils
import subprocess

from nanocamera import Camera
from dotenv import load_dotenv
from urllib.parse import urlparse
from utilities import EnvArgumentParser



class TritonRemoteModel:
    def __init__(self, url: str, model: str):
        parsed_url = urlparse(url)
        if parsed_url.scheme == "grpc":
            from tritonclient.grpc import InferenceServerClient, InferInput

            self.client = InferenceServerClient(parsed_url.netloc)
            self.model_name = model
            self.metadata = self.client.get_model_metadata(self.model_name, as_json=True)
            self.config = self.client.get_model_config(self.model_name, as_json=True)["config"]

            def create_input_placeholders() -> typing.List[InferInput]:
                return [
                    InferInput(
                        i['name'],
                        [int(s) for s in i['shape']],
                        i['datatype']
                    )
                    for i in self.metadata['inputs']
                ]

        elif parsed_url.scheme == "http":
            from tritonclient.http import InferenceServerClient, InferInput

            self.client = InferenceServerClient(parsed_url.netloc)
            self.model_name = model
            self.metadata = self.client.get_model_metadata(self.model_name)
            self.config = self.client.get_model_config(self.model_name)

            def create_input_placeholders() -> typing.List[InferInput]:
                return [
                    InferInput(
                        i['name'],
                        [int(s) for s in i['shape']],
                        i['datatype']
                    )
                    for i in self.metadata['inputs']
                ]

        else:
            raise "Unsupported protocol. Use HTTP or GRPC."

        self._create_input_placeholders_fn = create_input_placeholders
        self.model_dims = self._get_dims()
        self.classes = self._get_classes()

    @property
    def runtime(self):
        return self.metadata.get("backend", self.metadata.get("platform"))

    def __call__(self, *args, **kwargs) -> typing.Union[torch.Tensor, typing.Tuple[torch.Tensor, ...]]:
        inputs = self._create_inputs(*args, **kwargs)
        response = self.client.infer(model_name=self.model_name, inputs=inputs)
        result = []
        for output in self.metadata['outputs']:
            tensor = torch.tensor(response.as_numpy(output['name']))
            result.append(tensor)
        return result[0][0] if len(result) == 1 else result

    def _create_inputs(self, *args, **kwargs):
        args_len, kwargs_len = len(args), len(kwargs)
        if not args_len and not kwargs_len:
            raise RuntimeError("No inputs provided.")
        if args_len and kwargs_len:
            raise RuntimeError("Cannot specify args and kwargs at the same time")

        placeholders = self._create_input_placeholders_fn()

        if args_len:
            if args_len != len(placeholders):
                raise RuntimeError(f"Expected {len(placeholders)} inputs, got {args_len}.")
            for input, value in zip(placeholders, args):
                input.set_data_from_numpy(value)
        else:
            for input in placeholders:
                value = kwargs[input.name]
                input.set_data_from_numpy(value)
        return placeholders

    def _get_classes(self):
        label_filename = self.config["output"][0]["label_filename"]
        docker_file_path = f"/root/app/triton/{self.model_name}/{label_filename}"
        jetson_file_path = os.path.join(os.path.abspath(os.getcwd()), f"triton/{self.model_name}/{label_filename}")

        if os.path.isfile(docker_file_path):
            with open(docker_file_path, "r") as file:
                classes = file.read().splitlines()
        elif os.path.isfile(jetson_file_path):
            with open(jetson_file_path, "r") as file:
                classes = file.read().splitlines()
        else:
            classes = None

        return classes

    def _get_dims(self):
        try:
            model_dims = tuple(self.config["input"][0]["dims"][2:4])
            return tuple(map(int, model_dims))
        except:
            return (640, 640)


class ObjectDetection():
    def __init__(
            self,
            model_name,
            camera_width,
            camera_height,
            triton_url
        ):
 
        try:
            self.model = TritonRemoteModel(url=triton_url, model=model_name)
        except ConnectionError as e:
            raise f"Failed to connect to Triton: {e}"

        self.frame_dims = [camera_width, camera_height]

    def __call__(self, frame):
        predictions = self.model(
            frame,
            numpy.array(self.frame_dims, dtype='int16')
        ).tolist()

        if len(predictions) > 1:
            bboxes = [item[:4] for item in predictions]
            confs = [round(float(item[4]), 2) for item in predictions]
            indexes = [int(item[5]) for item in predictions]
        else:
            bboxes = predictions[:4]
            confs = round(float(predictions[4]), 2)
            indexes = int(predictions[5])

        return bboxes, confs, indexes


class Annotator():
    def __init__(self, classes):
        self.classes = classes
        self.colors = list(numpy.random.rand(len(self.classes), 3) * 255)
        self.santa_hat = cv2.cvtColor(cv2.imread("images/santa_hat.png"), cv2.COLOR_BGR2RGB)
        self.santa_hat_mask = cv2.cvtColor(cv2.imread("images/santa_hat_mask.png"), cv2.COLOR_BGR2RGB)

    def __call__(self, frame, bboxes, confs, indexes):
        for i in range(len(bboxes)):
            xmin, ymin, xmax, ymax = bboxes[i]
            color = self.colors[indexes[i]]
            frame = cv2.rectangle(
                img=frame,
                pt1=(xmin, ymin),
                pt2=(xmax, ymax),
                color=color,
                thickness=2
            )

            frame = cv2.putText(
                img=frame,
                text=f'{self.classes[indexes[i]]} ({str(confs[i])})',
                org=(xmin, ymin),
                fontFace=cv2.FONT_HERSHEY_PLAIN ,
                fontScale=0.75,
                color=color,
                thickness=1,
                lineType=cv2.LINE_AA
            )

        return frame

    def _overlay_obj(self, frame, bbox):
        resize_width = bbox[3]-bbox[1]
        santa_hat = imutils.resize(self.santa_hat.copy(), width=resize_width)
        santa_hat_mask = imutils.resize(self.santa_hat_mask.copy(), width=resize_width)

        x, y = bbox[:2]
        bg = frame.copy()
        h_bg, w_bg = bg.shape[0], bg.shape[1]
        h, w = santa_hat.shape[0], santa_hat.shape[1]
        
        mask_boolean = santa_hat_mask[:,:,0] == 0
        mask_rgb_boolean = numpy.stack([mask_boolean, mask_boolean, mask_boolean], axis=2)
        
        if x >= 0 and y >= 0:
            h_part = h - max(0, y+h-h_bg)
            w_part = w - max(0, x+w-w_bg)
            bg[y:y+h_part, x:x+w_part, :] = bg[y:y+h_part, x:x+w_part, :] * ~mask_rgb_boolean[0:h_part, 0:w_part, :] + (santa_hat * mask_rgb_boolean)[0:h_part, 0:w_part, :]
            
        elif x < 0 and y < 0:
            h_part = h + y
            w_part = w + x
            bg[0:0+h_part, 0:0+w_part, :] = bg[0:0+h_part, 0:0+w_part, :] * ~mask_rgb_boolean[h-h_part:h, w-w_part:w, :] + (santa_hat * mask_rgb_boolean)[h-h_part:h, w-w_part:w, :]
            
        elif x < 0 and y >= 0:
            h_part = h - max(0, y+h-h_bg)
            w_part = w + x
            bg[y:y+h_part, 0:0+w_part, :] = bg[y:y+h_part, 0:0+w_part, :] * ~mask_rgb_boolean[0:h_part, w-w_part:w, :] + (santa_hat * mask_rgb_boolean)[0:h_part, w-w_part:w, :]
            
        elif x >= 0 and y < 0:
            h_part = h + y
            w_part = w - max(0, x+w-w_bg)
            bg[0:0+h_part, x:x+w_part, :] = bg[0:0+h_part, x:x+w_part, :] * ~mask_rgb_boolean[h-h_part:h, 0:w_part, :] + (santa_hat * mask_rgb_boolean)[h-h_part:h, 0:w_part, :]
        
        return bg

    def santa_hat_plugin(self, frame, bboxes, confs):
        max_index = max(range(len(confs)), key=confs.__getitem__)
        return self._overlay_obj(frame, bboxes[max_index])
    


def main(
    object_detection,
    triton_url,
    model_name,
    classes,
    confidence_threshold,
    iou_threshold,
    stream_ip,
    stream_port,
    stream_application,
    stream_key,
    camera_index,
    camera_width,
    camera_height,
    camera_fps
):

    rtmp_url = "rtmp://{}:{}/{}/{}".format(
        stream_ip,
        stream_port,
        stream_application,
        stream_key
    )

    camera = Camera(
        device_id=camera_index,
        flip=0,
        width=camera_width,
        height=camera_height,
        fps=camera_fps
    )

    command = [
        'ffmpeg',
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-s', "{}x{}".format(camera_width, camera_height),
        '-r', str(camera_fps),
        '-i', '-',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-preset', 'ultrafast',
        '-f', 'flv',
        rtmp_url
    ]

    p = subprocess.Popen(command, stdin=subprocess.PIPE)

    if object_detection:
        model = ObjectDetection(
            model_name=model_name,
            camera_width=camera_width,
            camera_height=camera_height,
            triton_url=triton_url
        )

        annotator = Annotator(model.model.classes)

        period = 10
        tracking_index = 0

        while camera.isReady():
            frame = camera.read()

            if tracking_index % period == 0:
                bboxes, confs, indexes = model(frame)
                tracking_index = 0
            
            frame = annotator.santa_hat_plugin(frame, bboxes, confs)
            # frame = annotator(frame, bboxes, confs, indexes)
            tracking_index += 1

            p.stdin.write(frame.tobytes())

    else:
        while camera.isReady():
            frame = camera.read()
            p.stdin.write(frame.tobytes())

    camera.release()
    del camera


if __name__ == "__main__":
    load_dotenv()
    parser = EnvArgumentParser()
    parser.add_arg("OBJECT_DETECTION", default=True, type=bool)
    parser.add_arg("TRITON_URL", default="grpc://localhost:8001", type=str)
    parser.add_arg("MODEL", default="yolov5n", type=str)
    parser.add_arg("CLASSES", default=None, type=list)
    parser.add_arg("CONFIDENCE_THRESHOLD", default=0.3, type=float)
    parser.add_arg("IOU_THRESHOLD", default=0.45, type=float)
    parser.add_arg("STREAM_IP", default="127.0.0.1", type=str)
    parser.add_arg("STREAM_PORT", default=1935, type=int)
    parser.add_arg("STREAM_APPLICATION", default="live", type=str)
    parser.add_arg("STREAM_KEY", default="stream", type=str)
    parser.add_arg("CAMERA_INDEX", default=0, type=int)
    parser.add_arg("CAMERA_WIDTH", default=640, type=int)
    parser.add_arg("CAMERA_HEIGHT", default=480, type=int)
    parser.add_arg("CAMERA_FPS", default=30, type=int)
    args = parser.parse_args()

    main(
        args.OBJECT_DETECTION,
        args.TRITON_URL,
        args.MODEL,
        args.CLASSES,
        args.CONFIDENCE_THRESHOLD,
        args.IOU_THRESHOLD,
        args.STREAM_IP,
        args.STREAM_PORT,
        args.STREAM_APPLICATION,
        args.STREAM_KEY,
        args.CAMERA_INDEX,
        args.CAMERA_WIDTH,
        args.CAMERA_HEIGHT,
        args.CAMERA_FPS
    )


# postprocess warmup
# model_warmup [
#   {
#     name : "postprocess model warmup"
#     batch_size: 1
#     count: 1
#     inputs {
#       key: "INPUT_0"
#       value: {
#           data_type: TYPE_FP32
#           dims: 1
#           dims: 25200
#           dims: 85
#           input_data_file: "INPUT_0"
#       }
#     }
#     inputs {
#       key: "INPUT_1"
#       value: {
#           data_type: TYPE_INT16
#           dims: 2
#           input_data_file: "INPUT_1"
#       }
#     }
#   }
# ]

# preprocess warmup
# model_warmup [{
#     name : "preprocess model warmup"
#     batch_size: 1
#     count: 1
#     inputs {
#       key: "INPUT_0"
#       value: {
#         data_type: TYPE_UINT8
#         dims: 1280
#         dims: 720
#         dims: 3
#         input_data_file: "INPUT_0"
#       }
#     }
# }]