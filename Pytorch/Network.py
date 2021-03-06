# -*- coding: utf-8 -*-

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import time


cfg = {'PicaNet': "GGLLL",
       'Size': [28, 28, 28, 56, 112, 224],
       'Channel': [1024, 512, 512, 256, 128, 64],
       'loss_ratio': [0.5, 0.5, 0.5, 0.8, 0.8, 1]}


class Unet(nn.Module):
    def __init__(self, cfg={'PicaNet': "GGLLL",
       'Size': [28, 28, 28, 56, 112, 224],
       'Channel': [1024, 512, 512, 256, 128, 64],
       'loss_ratio': [0.5, 0.5, 0.5, 0.8, 0.8, 1]}):
        super(Unet, self).__init__()
        self.encoder = Encoder()
        self.decoder = nn.ModuleList()
        self.cfg = cfg
        for i in range(5):
            assert cfg['PicaNet'][i] == 'G' or cfg['PicaNet'][i] == 'L'
            self.decoder.append(
                DecoderCell(size=cfg['Size'][i],
                            in_channel=cfg['Channel'][i],
                            out_channel=cfg['Channel'][i + 1],
                            mode=cfg['PicaNet'][i]).cuda())
        self.decoder.append(DecoderCell(size=cfg['Size'][5],
                                        in_channel=cfg['Channel'][5],
                                        out_channel=1,
                                        mode='C').cuda())

    def forward(self, *input):
        if len(input) == 2:
            x = input[0]
            tar = input[1]
            test_mode = False
        if len(input) == 3:
            x = input[0]
            tar = input[1]
            test_mode = input[2]
        if len(input) == 1:
            x = input[0]
            tar = None
            test_mode = True
        En_out = self.encoder(x)
        Dec = None
        pred = []
        for i in range(6):
            # print(En_out[5 - i].size())
            Dec, _pred = self.decoder[i](En_out[5 - i], Dec)
            pred.append(_pred)
        loss = 0
        if not test_mode:
            for i in range(6):
                loss += F.binary_cross_entropy(pred[5 - i], tar) * self.cfg['loss_ratio'][5 - i]
                # print(float(loss))
                if tar.size()[2] > 28:
                    tar = F.max_pool2d(tar, 2, 2)
        return pred, loss


def make_layers(cfg, in_channels):
    layers = []
    for v in cfg:
        if v == 'M':
            layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
        elif v == 'm':
            layers += [nn.MaxPool2d(kernel_size=1, stride=1)]
        else:
            conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=1)
            layers += [conv2d, nn.ReLU(inplace=True)]
            in_channels = v
    return nn.Sequential(*layers)


# [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512, 'M', 512, 512, 512, 'M']

class Encoder(nn.Module):
    def __init__(self):
        super(Encoder, self).__init__()
        configure = [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512, 'm', 512, 512, 512, 'm']
        self.seq = make_layers(configure, 3)
        self.conv6 = nn.Conv2d(512, 1024, kernel_size=3, stride=1, padding=12, dilation=12)  # fc6 in paper
        self.conv7 = nn.Conv2d(1024, 1024, 3, 1, 1)  # fc7 in paper

    def forward(self, *input):
        x = input[0]
        conv1 = self.seq[:4](x)
        conv2 = self.seq[4:9](conv1)
        conv3 = self.seq[9:16](conv2)
        conv4 = self.seq[16:23](conv3)
        conv5 = self.seq[23:](conv4)
        conv6 = self.conv6(conv5)
        conv7 = self.conv7(conv6)

        return conv1, conv2, conv3, conv4, conv5, conv7


class DecoderCell(nn.Module):
    def __init__(self, size, in_channel, out_channel, mode):
        super(DecoderCell, self).__init__()
        self.bn_en = nn.BatchNorm2d(in_channel)
        self.conv1 = nn.Conv2d(2 * in_channel, in_channel, kernel_size=3, padding=1)  # not specified in paper
        self.mode = mode
        if mode == 'G':
            self.picanet = PiCANet_G(size, in_channel)
        elif mode == 'L':
            self.picanet = PiCANet_L(in_channel)
        elif mode == 'C':
            self.picanet = None
        else:
            assert 0
        if not mode == 'C':
            self.conv2 = nn.Conv2d(2 * in_channel, out_channel, kernel_size=3, padding=1)  # not specified in paper
            self.bn_feature = nn.BatchNorm2d(out_channel)
            self.conv3 = nn.Conv2d(out_channel, 1, kernel_size=1, padding=0)  # not specified in paper
        else:
            self.conv2 = nn.Conv2d(in_channel, 1, kernel_size=3, padding=1)

    def forward(self, *input):
        assert len(input) <= 2
        if input[1] is None:
            En = input[0]
            Dec = input[0]  # not specified in paper
        else:
            En = input[0]
            Dec = input[1]

        if Dec.size()[2] * 2 == En.size()[2]:
            Dec = F.upsample(Dec, scale_factor=2, mode='bilinear', align_corners=True)
        elif Dec.size()[2] != En.size()[2]:
            assert 0
        En = self.bn_en(En)
        En = F.relu(En)
        fmap = torch.cat((En, Dec), dim=1)  # F
        fmap = self.conv1(fmap)
        fmap = F.relu(fmap)
        if not self.mode == 'C':
            # print(fmap.size())
            fmap_att = self.picanet(fmap)  # F_att
            x = torch.cat((fmap, fmap_att), 1)
            x = self.conv2(x)
            x = self.bn_feature(x)
            Dec_out = F.relu(x)
            _y = self.conv3(Dec_out)
            _y = F.sigmoid(_y)
        else:
            Dec_out = self.conv2(fmap)
            _y = F.sigmoid(Dec_out)

        return Dec_out, _y


class PiCANet_G(nn.Module):
    def __init__(self, size, in_channel):
        super(PiCANet_G, self).__init__()
        self.renet = Renet(size, in_channel, 100)
        self.in_channel = in_channel

    def forward(self, *input):
        x = input[0]
        size = x.size()
        kernel = self.renet(x)
        kernel = F.softmax(kernel, 1)
        # print(kernel.size())
        kernel = kernel.reshape(size[0] * size[2] * size[3], 1, 1, 10, 10)
        x = torch.unsqueeze(x, 0)
        x = F.conv3d(input=x, weight=kernel, bias=None, stride=1, padding=0, dilation=(1, 3, 3), groups=size[0])
        # print(torch.cuda.memory_allocated() / 1024 / 1024)
        x = torch.reshape(x, (size[0], size[1], size[2], size[3]))
        # print(torch.cuda.memory_allocated() / 1024 / 1024)
        return x


class PiCANet_L(nn.Module):
    def __init__(self, in_channel):
        super(PiCANet_L, self).__init__()
        self.conv1 = nn.Conv2d(in_channel, 128, kernel_size=7, dilation=2, padding=6)
        self.conv2 = nn.Conv2d(128, 49, kernel_size=1)

    def forward(self, *input):
        x = input[0]
        size = x.size()
        kernel = self.conv1(x)
        kernel = self.conv2(kernel)
        kernel = F.softmax(kernel, 1)
        kernel = torch.reshape(kernel, (size[0] * size[2] * size[3], 1, 1, 7, 7))
        # fmap = []
        # x = torch.unsqueeze(x, 0)
        x = F.pad(x, (6, 6, 6, 6))
        # print(torch.cuda.memory_allocated() / 1024 / 1024)
        patch = x.unfold(2, 13, 1).unfold(3, 13, 1).contiguous().view(1, -1, size[1], 13, 13)
        # print(torch.cuda.memory_allocated() / 1024 / 1024)
        x = F.conv3d(input=patch, weight=kernel, bias=None, stride=1, padding=0, dilation=(1, 2, 2),
                     groups=size[0] * size[2] * size[3])
        x = x.view(size[0], size[1], size[2], size[3])
        """
        for i in range(size[2]):
            for j in range(size[3]):
                print(torch.cuda.memory_allocated() / 1024 / 1024)
                pix = F.conv3d(input=F.pad(x, (6 - j, 7 + j - size[3], 6 - i, 7 + i - size[2])),
                               weight=kernel[:, :, :, :, :, i, j],
                               dilation=(1, 2, 2), groups=size[0])
                print(torch.cuda.memory_allocated() / 1024 / 1024)
                fmap.append(pix)
        x = torch.cat(fmap, 3)
        x = torch.reshape(x, (size[0], size[1], size[2], size[3]))
        """
        return x


class Renet(nn.Module):
    def __init__(self, size, in_channel, out_channel):
        super(Renet, self).__init__()
        self.size = size
        self.in_channel = in_channel
        self.out_channel = out_channel
        self.vertical = nn.LSTM(input_size=in_channel, hidden_size=256, batch_first=True,
                                bidirectional=True)  # each row
        self.horizontal = nn.LSTM(input_size=512, hidden_size=256, batch_first=True,
                                  bidirectional=True)  # each column
        self.conv = nn.Conv2d(512, out_channel, 1)
        # self.fc = nn.Linear(512 * size * size, 10)

    def forward(self, *input):
        x = input[0]
        temp = []
        size = x.size()  # batch, in_channel, height, width
        x = torch.transpose(x, 1, 3)  # batch, width, height, in_channel
        for i in range(self.size):
            h, _ = self.vertical(x[:, :, i, :])
            temp.append(h)  # batch, width, 512
        x = torch.stack(temp, dim=2)  # batch, width, height, 512
        temp = []
        for i in range(self.size):
            h, _ = self.horizontal(x[:, i, :, :])
            temp.append(h)  # batch, width, 512
        x = torch.stack(temp, dim=3)  # batch, height, 512, width
        x = torch.transpose(x, 1, 2)  # batch, 512, height, width
        # x = torch.reshape(x, (-1, 512 * self.size * self.size))
        x = self.conv(x)
        return x


if __name__ == '__main__':
    vgg = torchvision.models.vgg16(pretrained=True)
    # model = Encoder()
    # model.seq.load_state_dict(vgg.features.state_dict())
    # print(model.state_dict().keys())
    # print(vgg.features.state_dict().keys())
    # print(vgg.features)
    device = torch.device("cuda")
    batch_size = 1
    noise = torch.randn((batch_size, 3, 224, 224)).type(torch.cuda.FloatTensor)
    target = torch.randn((batch_size, 1, 224, 224)).type(torch.cuda.FloatTensor)

    # print(vgg.features(noise))
    # print(model(noise))
    # print(model.seq)
    # print(vgg.features)
    # print(F.mse_loss(model.seq[:8](noise), vgg.features[:8](noise)))
    model = Unet(cfg).cuda()
    model.encoder.seq.load_state_dict(vgg.features.state_dict())
    opt = torch.optim.Adam(model.parameters(), lr=0.001)
    print('Time: {}'.format(time.clock()))
    _, loss = model(noise, target)
    loss.backward()
    """
    for i in range(1000):
        opt.zero_grad()
        time_spend = time.clock()
        _, loss = model(noise, target)
        print('Time_Spend: {}'.format(time.clock() - time_spend))
        loss.backward()
        opt.step()

        print(float(loss))
        print('Time: {}'.format(time.clock()))
    """

